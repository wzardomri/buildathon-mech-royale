"""First-pass ground routing and survival policy for the fictional arena."""

from array import array
import heapq
import json
import math
from pathlib import Path
import sys

import mechfight as mf


def distance_to_edge(point, first, second):
    dx, dy = second[0] - first[0], second[1] - first[1]
    length = dx * dx + dy * dy
    if not length:
        return math.dist(point, first)
    along = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / length
    along = max(0.0, min(1.0, along))
    return math.hypot(point[0] - first[0] - along * dx, point[1] - first[1] - along * dy)


class Polygon:
    def __init__(self, ring):
        points = tuple(zip(ring[::2], ring[1::2]))
        self.edges = tuple(zip(points, points[1:] + points[:1]))
        self.bounds = tuple(fn(axis) for axis in zip(*points) for fn in (min, max))

    def intersects_disc(self, point, radius):
        if self.contains(point):
            return True
        return any(distance_to_edge(point, first, second) <= radius for first, second in self.edges)

    def contains_disc(self, point, radius):
        if not self.contains(point):
            return False
        return all(distance_to_edge(point, first, second) > radius for first, second in self.edges)

    def contains(self, point):
        x, y = point
        inside = False
        for (ax, ay), (bx, by) in self.edges:
            if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
                inside = not inside
        return inside

    def crossings(self, point, delta):
        px, py = point
        dx, dy = delta
        for (ax, ay), (bx, by) in self.edges:
            ex, ey = bx - ax, by - ay
            denominator = dx * ey - dy * ex
            if abs(denominator) < 1e-9:
                continue
            lx, ly = ax - px, ay - py
            edge_ratio = (lx * dy - ly * dx) / denominator
            if 0.0 <= edge_ratio <= 1.0:
                yield (lx * ey - ly * ex) / denominator


class Prism:
    def __init__(self, value):
        self.outline = Polygon(value["ring"])
        self.holes = tuple(Polygon(ring) for ring in value["holes"])
        self.base = value["base"]
        self.top = value["top"]

    def blocks_ray(self, start, end):
        x, y, z = start
        dx, dy, dz = (b - a for a, b in zip(start, end))
        low, high = 0.0, 1.0
        if abs(dz) < 1e-9:
            if z < self.base or z > self.top:
                return False
        else:
            floor, ceiling = sorted(((self.base - z) / dz, (self.top - z) / dz))
            low, high = max(low, floor), min(high, ceiling)
            if low >= high:
                return False

        a, b = (x + dx * low, y + dy * low), (x + dx * high, y + dy * high)
        min_x, max_x, min_y, max_y = self.outline.bounds
        if max(a[0], b[0]) < min_x or min(a[0], b[0]) > max_x:
            return False
        if max(a[1], b[1]) < min_y or min(a[1], b[1]) > max_y:
            return False

        # The material changes only where the ray crosses an outline or a hole.
        intervals = [low, high]
        for polygon in (self.outline, *self.holes):
            intervals.extend(t for t in polygon.crossings((x, y), (dx, dy)) if low < t < high)
        intervals.sort()
        for first, last in zip(intervals, intervals[1:]):
            middle = (first + last) / 2.0
            point = x + dx * middle, y + dy * middle
            if self.outline.contains(point) and not any(hole.contains(point) for hole in self.holes):
                return True
        return False


class GroundMap:
    GRID = 6.0
    RAY_BUCKET = 24.0
    NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))

    def __init__(self, path, stats):
        world = json.loads((path / "world.json").read_text())
        self.terrain = world["terrain"]
        self.heights = array("h", (path / "terrain.i16").read_bytes())
        if sys.byteorder != "little":
            self.heights.byteswap()
        bounds = world["bounds"]
        self.min_x = bounds["minX"] + self.GRID / 2
        self.min_y = bounds["minY"] + self.GRID / 2
        self.width = 1 + int((bounds["maxX"] - self.GRID / 2 - self.min_x) / self.GRID)
        self.depth = 1 + int((bounds["maxY"] - self.GRID / 2 - self.min_y) / self.GRID)
        self.radius = stats["radius"] + 0.5
        self.max_slope = math.tan(math.radians(stats["max_slope_deg"]))
        self.points = {(x, y): self.point((x, y)) for x in range(self.width) for y in range(self.depth)}
        self.elevation = {cell: self.ground(*point) for cell, point in self.points.items()}
        blocked = set()
        self.ray_buckets = {}
        for value in world["prisms"]:
            prism = Prism(value)
            shape, holes = prism.outline, prism.holes
            min_x, max_x, min_y, max_y = shape.bounds
            for key in self.ray_bucket_keys(min_x, max_x, min_y, max_y):
                self.ray_buckets.setdefault(key, []).append(prism)
            first_x, first_y = self.cell((min_x - self.radius, min_y - self.radius))
            last_x, last_y = self.cell((max_x + self.radius, max_y + self.radius))
            for x in range(max(0, first_x), min(self.width, last_x + 1)):
                for y in range(max(0, first_y), min(self.depth, last_y + 1)):
                    cell = (x, y)
                    ground = self.elevation[cell]
                    if prism.base >= ground + stats["height"] or prism.top <= ground + stats["step_up"]:
                        continue
                    point = self.points[cell]
                    if shape.intersects_disc(point, self.radius) and not any(
                        hole.contains_disc(point, self.radius) for hole in holes
                    ):
                        blocked.add(cell)
        self.open = self.points.keys() - blocked
        self.edges = {cell: tuple(self.walk_neighbors(cell)) for cell in self.open}

    def ray_bucket_keys(self, min_x, max_x, min_y, max_y):
        step = self.RAY_BUCKET
        for x in range(math.floor(min_x / step), math.floor(max_x / step) + 1):
            for y in range(math.floor(min_y / step), math.floor(max_y / step) + 1):
                yield x, y

    def blocked_between(self, start, end):
        (x, y, z), (tx, ty, tz) = start, end
        min_x, max_x = sorted((x, tx))
        min_y, max_y = sorted((y, ty))
        prisms = set()
        for key in self.ray_bucket_keys(min_x, max_x, min_y, max_y):
            prisms.update(self.ray_buckets.get(key, ()))
        if any(prism.blocks_ray(start, end) for prism in prisms):
            return True
        steps = max(2, math.ceil(math.hypot(tx - x, ty - y) / 3.0))
        for step in range(1, steps):
            ratio = step / steps
            ground = self.ground(x + (tx - x) * ratio, y + (ty - y) * ratio)
            if ground > z + (tz - z) * ratio:
                return True
        return False

    def ground(self, x, y):
        terrain = self.terrain
        gx = (x - terrain["minX"]) / terrain["step"]
        gy = (y - terrain["minY"]) / terrain["step"]
        nx, ny = terrain["nx"], terrain["ny"]
        i = min(max(math.floor(gx), 0), nx - 2)
        j = min(max(math.floor(gy), 0), ny - 2)
        dx, dy = max(0.0, min(1.0, gx - i)), max(0.0, min(1.0, gy - j))
        base = j * nx + i
        south = self.heights[base] * (1.0 - dx) + self.heights[base + 1] * dx
        north = self.heights[base + nx] * (1.0 - dx) + self.heights[base + nx + 1] * dx
        return (south * (1.0 - dy) + north * dy) * terrain["scale"]

    def cell(self, point):
        return round((point[0] - self.min_x) / self.GRID), round((point[1] - self.min_y) / self.GRID)

    def point(self, cell):
        return self.min_x + cell[0] * self.GRID, self.min_y + cell[1] * self.GRID

    def nearest_open(self, point, search=4):
        x, y = self.cell(point)
        if (x, y) in self.open:
            return x, y
        nearby = (
            cell for i in range(x - search, x + search + 1)
            for j in range(y - search, y + search + 1)
            if (cell := (i, j)) in self.open
        )
        return min(nearby, key=lambda cell: math.dist(point, self.points[cell]), default=None)

    def walk_neighbors(self, cell):
        x, y = cell
        for dx, dy in self.NEIGHBORS:
            destination = x + dx, y + dy
            if destination not in self.open:
                continue
            if dx and dy and ((x + dx, y) not in self.open or (x, y + dy) not in self.open):
                continue
            length = self.GRID * math.hypot(dx, dy)
            rise = self.elevation[destination] - self.elevation[cell]
            if rise <= self.max_slope * length:
                yield destination, length + 0.8 * max(0.0, rise)

    def route(self, position, goal):
        start, finish = self.nearest_open(position), self.nearest_open(goal, search=8)
        if start is None or finish is None:
            return []
        if start == finish:
            return [self.points[start]]
        finish_point = self.points[finish]
        start_distance = math.dist(self.points[start], finish_point)
        distances = {start: 0.0}
        parents = {}
        queue = [(start_distance, 0.0, start)]
        closest, remaining = start, start_distance
        for _ in range(1_500):
            if not queue:
                break
            _, cost, cell = heapq.heappop(queue)
            if cost != distances[cell]:
                continue
            left = math.dist(self.points[cell], finish_point)
            if left < remaining:
                closest, remaining = cell, left
            if cell == finish:
                break
            for neighbor, step in self.edges[cell]:
                total = cost + step
                if total < distances.get(neighbor, math.inf):
                    distances[neighbor] = total
                    parents[neighbor] = cell
                    priority = total + 1.45 * math.dist(self.points[neighbor], finish_point)
                    heapq.heappush(queue, (priority, total, neighbor))
        route = []
        while closest != start:
            route.append(self.points[closest])
            closest = parents[closest]
        route.reverse()
        return route


def unit_toward(position, goal):
    dx, dy = goal[0] - position[0], goal[1] - position[1]
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length else (0.0, 0.0)


def within_radius(position, center, radius):
    if math.dist(position, center) <= radius:
        return position
    dx, dy = unit_toward(center, position)
    return center[0] + radius * dx, center[1] + radius * dy


class Bot:
    name = "solid-cover-v1"
    SEEK_RESOURCES = True
    UPGRADE_PRIORITY = {
        "armor": 0, "nanites": 1, "servos": 2, "plating": 3,
        "reactor": 4, "capacitor": 5, "sensors": 6,
    }

    def __init__(self, hello):
        config = hello["config"]
        self.world = GroundMap(Path(hello["map"]["dir"]) / "v2", config["mech"])
        self.waypoints = []
        self.last_goal = None
        self.replanned_at = -math.inf
        self.stalled_for = 0
        self.previous_move = (0.0, 0.0)
        self.recovery_until = 0.0
        self.recovery_heading = (0.0, 0.0)
        self.last_damage_at = -math.inf
        self.last_damage_bearing = None
        self.hit_at = -math.inf
        self.cover_goal = None
        self.cover_checked_at = -math.inf
        self.sprinting = False
        self.scanning_offset = (hello["seed"] % 1000) / 1000.0 * 2.0 * math.pi

    def threat_sources(self, obs, position, enemies):
        now = obs["t"]
        for event in obs["events"]:
            if event["type"] == "damaged" and event["bearing"] is not None:
                self.last_damage_at = now
                self.last_damage_bearing = event["bearing"]
            elif event["type"] == "hit" and event["target"] < 100:
                self.hit_at = now

        threats = [
            (enemy["pos"][0], enemy["pos"][1], enemy["pos"][2] + 7.0)
            for enemy in enemies[:2] if math.dist(position, enemy["pos"][:2]) < 155.0
        ]
        if not threats and now - self.last_damage_at < 3.0 and self.last_damage_bearing is not None:
            x = position[0] + 75.0 * math.cos(self.last_damage_bearing)
            y = position[1] + 75.0 * math.sin(self.last_damage_bearing)
            threats.append((x, y, self.world.ground(x, y) + 7.0))
        return threats

    def cover_position(self, position, threats, zone_center, zone_radius):
        nearest = threats[0]
        heading = math.atan2(position[1] - nearest[1], position[0] - nearest[0])
        best, best_score = None, -math.inf
        candidates = {self.world.cell(position): position}
        for distance in (18.0, 33.0):
            for angle in (0.0, 0.65, -0.65, 1.3, -1.3):
                x = position[0] + distance * math.cos(heading + angle)
                y = position[1] + distance * math.sin(heading + angle)
                projected = within_radius((x, y), zone_center, zone_radius)
                cell = self.world.nearest_open(projected, search=2)
                if cell is not None:
                    candidates.setdefault(cell, self.world.points[cell])

        for point in candidates.values():
            if math.dist(point, zone_center) > zone_radius + 3.0:
                continue
            elevation = self.world.ground(*point) + 5.0
            covered = sum(self.world.blocked_between(source, (*point, elevation)) for source in threats)
            closest = min(math.dist(source[:2], point) for source in threats)
            score = 42.0 * covered + 0.7 * closest - 0.55 * math.dist(position, point)
            if score > best_score:
                best, best_score = point, score
        return best

    def gun_action(self, action, me, enemy, distance, shoot):
        gun = me["weapons"][0]
        speed = gun["stats"].get("speed", 252.0) if gun else 252.0
        flight = distance / speed
        point = [target + velocity * flight for target, velocity in zip(enemy["pos"], enemy["vel"])]
        point[2] += 4.5 * (1.0 + 0.05 * enemy.get("cubes", 0))
        muzzle = (*me["pos"][:2], me["pos"][2] + me["stats"]["muzzle_height"])
        dx, dy, dz = [target - origin for target, origin in zip(point, muzzle)]
        yaw = math.atan2(dy, dx)
        pitch = math.atan2(dz, math.hypot(dx, dy))
        pitch = min(me["stats"]["max_pitch"], max(me["stats"]["min_pitch"], pitch))
        action["aim"] = [yaw, pitch]
        aimed = abs(math.remainder(yaw - me["aim"][0], 2 * math.pi)) < 0.075
        aimed = aimed and abs(pitch - me["aim"][1]) < 0.09
        if gun and shoot and aimed and not self.world.blocked_between(muzzle, point):
            action["fire"] = [True, False]

    def resource_goal(self, obs, position, enemies):
        me, zone = obs["self"], obs["zone"]
        if any(sound["dist"] == "near" and sound["kind"] in {"gun", "mortar", "lightning", "laser"}
               for sound in obs["sounds"]):
            return None

        current_center = tuple(zone["center"])
        current_radius = max(0.0, zone["radius"] - 3.0)
        next_center = tuple(zone["next_center"])
        next_radius = max(0.0, zone["next_radius"] - 4.0)
        walk_speed = me["stats"]["walk_speed"]

        def safely_inside(point):
            return math.dist(point, current_center) <= current_radius and math.dist(point, next_center) <= next_radius

        viable_drops = []
        for drop in obs["drops"]:
            point = tuple(drop["pos"][:2])
            distance = math.dist(position, point)
            if distance > 70.0 or (drop["holder"] is not None and drop["holder"] != me["id"]):
                continue
            if not safely_inside(point) or any(math.dist(enemy["pos"][:2], point) < 65.0 for enemy in enemies):
                continue
            if abs(me["pos"][2] - drop["pos"][2]) > 9.0:
                continue
            eta = 1.4 * distance / walk_speed
            if drop["lands_in"] > eta + 3.0:
                continue
            if me["hp"] < 0.85 * me["max_hp"] or me["energy"] < 0.5 * me["max_energy"] or me["cubes"] < 4:
                viable_drops.append((eta + drop["lands_in"] - drop["progress"], point))
        if viable_drops:
            return min(viable_drops)[1], "supply"

        viable_cubes = []
        if me["cubes"] < 10:
            for cube in obs["cubes"]:
                point = tuple(cube["pos"][:2])
                distance = math.dist(position, point)
                if distance <= 26.0 and abs(me["pos"][2] - cube["pos"][2]) < 4.0 and safely_inside(point):
                    viable_cubes.append((distance, point))
        return (min(viable_cubes)[1], "cube") if viable_cubes else None

    def act(self, obs):
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        position = tuple(me["pos"][:2])
        next_radius = max(0.0, zone["next_radius"] - min(18.0, zone["next_radius"] / 4.0))
        next_center = tuple(zone["next_center"])
        inside_goal = within_radius(position, next_center, next_radius)
        urgent = not me["in_zone"]
        goal = inside_goal
        mode = "rotate" if math.dist(position, inside_goal) >= 3.5 else "hold"
        if urgent:
            goal = within_radius(position, tuple(zone["center"]), max(0.0, zone["radius"] - 9.0))
            mode = "zone"

        enemies = sorted(
            (enemy for enemy in obs["mechs"] if enemy["kind"] == "mech"),
            key=lambda enemy: math.dist(me["pos"], enemy["pos"]),
        )
        nearest = enemies[0] if enemies else None
        distance = math.dist(me["pos"], nearest["pos"]) if nearest else math.inf
        threats = self.threat_sources(obs, position, enemies)
        health = me["hp"] / me["max_hp"]
        reset = health < 0.82 and now - max(self.last_damage_at, self.hit_at) < 7.0
        crowded = len(threats) >= 2 and obs["alive"] > 4
        retreat = distance < 32.0 or (bool(threats) and (reset or health < 0.72 or crowded))
        if retreat and not urgent and math.dist(position, inside_goal) < 20.0:
            if now - self.cover_checked_at >= 0.8:
                self.cover_goal = self.cover_position(position, threats, next_center, next_radius)
                self.cover_checked_at = now
            if self.cover_goal is not None:
                goal = self.cover_goal
                mode = "cover"
        else:
            self.cover_goal = None
            if self.SEEK_RESOURCES and mode == "hold" and distance > 100.0 and not reset:
                resource = self.resource_goal(obs, position, enemies)
                if resource is not None:
                    goal, mode = resource

        moving = math.hypot(*self.previous_move) > 0.1
        slow = math.hypot(*me["vel"][:2]) < 0.9
        self.stalled_for = self.stalled_for + 1 if moving and slow else 0
        if self.stalled_for >= 5 and now >= self.recovery_until:
            dx, dy = self.previous_move
            left = position[0] - dy * 8.0, position[1] + dx * 8.0
            self.recovery_heading = (-dy, dx) if self.world.cell(left) in self.world.open else (dy, -dx)
            self.recovery_until = now + 0.65
            self.waypoints = []
            self.last_goal = None

        recovering = now < self.recovery_until
        if recovering:
            move = self.recovery_heading
            mode = "unstuck"
        elif math.dist(position, goal) < 3.5:
            self.waypoints.clear()
            move = (0.0, 0.0)
        else:
            changed = self.last_goal is None or math.dist(goal, self.last_goal) > 18.0
            while self.waypoints and math.dist(position, self.waypoints[0]) < 2.7:
                self.waypoints.pop(0)
            if (changed or not self.waypoints) and now - self.replanned_at >= 0.45:
                self.waypoints = self.world.route(position, goal)
                self.last_goal = goal
                self.replanned_at = now
            waypoint = self.waypoints[0] if self.waypoints else goal
            desired = unit_toward(position, waypoint)
            # Slow near a waypoint so the continuous acceleration can make the turn.
            magnitude = min(1.0, math.dist(position, waypoint) / 5.0)
            move = desired[0] * magnitude, desired[1] * magnitude

        self.previous_move = move
        speed = math.hypot(*move)
        energy_floor = (6.0 if urgent else 23.0) if self.sprinting else (14.0 if urgent else 50.0)
        self.sprinting = (urgent or retreat) and me["energy"] >= energy_floor and speed > 0.8
        yaw = math.atan2(move[1], move[0]) if speed > 0.1 else math.remainder(now * 0.65 + self.scanning_offset, 2 * math.pi)
        action = {"move": list(move), "boost": self.sprinting, "aim": [yaw, 0.0], "jump": recovering and me["grounded"]}

        if nearest and distance < 155.0:
            shoot = distance < 120.0 and not urgent and (not retreat or distance < 18.0)
            self.gun_action(action, me, nearest, distance, shoot)
        elif self.last_damage_bearing is not None and now - self.last_damage_at < 2.0:
            action["aim"] = [self.last_damage_bearing, 0.0]

        gun = me["weapons"][0]
        if gun and gun["kind"] == "gun" and distance > 95.0 and not action.get("fire"):
            if gun.get("ammo", 12) < gun["stats"].get("mag", 12) * 0.5 and gun.get("reload", 0) == 0:
                action["reload"] = True

        if obs["tick"] % 10 == 0:
            mf.draw.label(f"{mode} | hp {me['hp']:.0f} | alive {obs['alive']}")
            if self.waypoints:
                point = self.waypoints[0]
                mf.draw.point([*point, self.world.ground(*point)], "#74dded", 4)

        if obs["offer"]:
            choices = obs["offer"]["choices"]
            action["pick"] = min(
                range(len(choices)),
                key=lambda i: self.UPGRADE_PRIORITY.get(choices[i]["id"], len(self.UPGRADE_PRIORITY)),
            )
        return action


if __name__ == "__main__":
    mf.run(Bot)
