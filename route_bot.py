"""First-pass ground routing and survival policy for the fictional arena."""

from array import array
import heapq
import json
import math
from pathlib import Path
import sys

import mechfight as mf
from weapon_control import target_body, weapon_plan


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
    name = "early-weapon-detours-v6"
    SEEK_RESOURCES = True
    PICKUP_PRIORITY = {"laser": 3, "lightning": 2, "mortar": 1}
    DETOUR_DISTANCE = {"laser": 36.0, "lightning": 26.0, "mortar": 19.0}
    UPGRADE_PRIORITY = {
        "armor": 0, "nanites": 1, "servos": 2, "plating": 3,
        "reactor": 4, "capacitor": 5, "sensors": 6,
    }

    def __init__(self, hello):
        config = hello["config"]
        self.config = config
        self.world = GroundMap(Path(hello["map"]["dir"]) / "v2", config["mech"])
        self.waypoints = []
        self.last_goal = None
        self.replanned_at = -math.inf
        self.stalled_for = 0
        self.previous_move = (0.0, 0.0)
        self.recovery_until = 0.0
        self.recovery_heading = (0.0, 0.0)
        self.last_damage_at = -math.inf
        self.last_player_damage_at = -math.inf
        self.last_damage_bearing = None
        self.hit_at = -math.inf
        self.cover_goal = None
        self.cover_checked_at = -math.inf
        self.sprinting = False
        self.scanning_offset = (hello["seed"] % 1000) / 1000.0 * 2.0 * math.pi
        self.combat_goal = None
        self.combat_target = None
        self.combat_checked_at = -math.inf
        self.strafe_sign = 1 if hello["seed"] % 2 else -1

    def threat_sources(self, obs, position, enemies):
        now = obs["t"]
        for event in obs["events"]:
            if event["type"] == "damaged" and event["bearing"] is not None:
                self.last_damage_at = now
                self.last_damage_bearing = event["bearing"]
                if event["weapon"] != "npc":
                    self.last_player_damage_at = now
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

    def select_enemy(self, me, enemies):
        position = me["pos"][:2]

        def priority(enemy):
            distance = math.dist(me["pos"], enemy["pos"])
            hp = enemy.get("hp", enemy.get("max_hp", 1400))
            ex, ey = unit_toward(enemy["pos"][:2], position)
            rear = math.cos(enemy["aim"][0]) * ex + math.sin(enemy["aim"][0]) * ey < -0.2
            weak = hp <= 405.0 and distance < 145.0
            return distance + 0.041 * hp - 32 * weak - 12 * rear - 13 * (enemy["id"] == self.combat_target)

        options = [enemy for enemy in enemies if math.dist(me["pos"], enemy["pos"]) <= 190.0]
        return min(options, key=priority, default=None)

    def farm_target(self, obs, enemy_distance):
        me = obs["self"]
        if enemy_distance < 130.0 or me["hp"] < 0.74 * me["max_hp"] or me["cubes"] >= 7:
            return None
        if obs["t"] - self.last_player_damage_at < 4.0:
            return None
        if any(sound["dist"] == "near" and sound["kind"] in {"gun", "mortar", "lightning", "laser"}
               for sound in obs["sounds"]):
            return None

        zone = obs["zone"]
        targets = [dict(crate, kind="crate") for crate in obs["crates"]]
        targets.extend(npc for npc in obs["mechs"] if npc["kind"] == "npc")
        options = []
        for target in targets:
            point = target["pos"]
            distance = math.dist(me["pos"], point)
            if distance > 92.0 or math.dist(point[:2], zone["next_center"]) > zone["next_radius"] - 5.0:
                continue
            if abs(point[2] - self.world.ground(*point[:2])) > 3.2:
                continue
            reward = 27.0 if target["kind"] == "npc" else 8.0
            priority = distance + 0.055 * target.get("hp", 330) - reward
            options.append((priority, target["id"], target))
        return min(options)[2] if options else None

    def attack_position(self, obs, target, zone_center, zone_radius, harvest):
        me = obs["self"]
        position = tuple(me["pos"][:2])
        goal = target["pos"][:2]
        distance = math.dist(position, goal)
        body, _, _ = target_body(target, self.config)
        mx, my = unit_toward(position, goal)
        muzzle = (*position, me["pos"][2] + me["stats"]["muzzle_height"])
        clear = not self.world.blocked_between(muzzle, body)
        if harvest and clear and distance <= 65.0:
            return position

        if not clear or (distance > 110.0 and target.get("hp", 1400) < 480.0) or harvest:
            forward = 16.0 if distance > 30.0 else 5.0
        else:
            forward = -6.0 if distance < 27.0 else 0.0
        candidate_positions = []
        for side in (self.strafe_sign, -self.strafe_sign, 0):
            lateral = 16.0 * side
            point = position[0] + mx * forward - my * lateral, position[1] + my * forward + mx * lateral
            safe = within_radius(point, zone_center, zone_radius)
            cell = self.world.nearest_open(safe, search=2)
            if cell is None:
                continue
            adjusted = self.world.points[cell]
            if math.dist(adjusted, position) < 5.0:
                continue
            aim_point = (*adjusted, self.world.ground(*adjusted) + me["stats"]["muzzle_height"])
            if self.world.blocked_between(muzzle, aim_point):
                continue
            new_distance = math.dist(adjusted, goal)
            after_move_clear = not self.world.blocked_between(aim_point, body)
            merit = 38.0 * after_move_clear - 0.2 * abs(new_distance - max(22.0, distance - forward))
            if side == self.strafe_sign:
                merit += 2.0
            candidate_positions.append((merit, adjusted))
        if candidate_positions:
            return max(candidate_positions)[1]
        if harvest:
            shortened = position[0] + mx * min(distance, 18.0), position[1] + my * min(distance, 18.0)
            return within_radius(shortened, zone_center, zone_radius)
        return position

    def take_weapon(self, obs):
        me = obs["self"]
        if all(me["weapons"]):
            return None
        held = {weapon["kind"] for weapon in me["weapons"] if weapon}
        available = []
        for pickup in obs["pickups"]:
            weapon = pickup["kind"]
            if weapon in held or weapon not in self.PICKUP_PRIORITY:
                continue
            distance = math.dist(me["pos"][:2], pickup["pos"][:2])
            if distance <= self.config["loot"]["pickup_radius"] and abs(me["pos"][2] - pickup["pos"][2]) <= 4.0:
                available.append((self.PICKUP_PRIORITY[weapon], -distance, pickup["id"]))
        return max(available)[2] if available else None

    def early_weapon_detour(self, obs, position, distance_to_enemy):
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        before_shrink = zone["shrink_start"] - now
        if (me["weapons"][1] is not None or distance_to_enemy < 105.0 or not me["grounded"]
                or me["hp"] < 0.76 * me["max_hp"] or not me["in_zone"] or now > 48.0
                or before_shrink < 9.0 or now - self.last_player_damage_at < 3.0):
            return None

        next_radius = max(0.0, zone["next_radius"] - 9.0)
        current_return = max(0.0, math.dist(position, zone["next_center"]) - next_radius)
        held = {weapon["kind"] for weapon in me["weapons"] if weapon}
        options = []
        for pickup in obs["pickups"]:
            weapon = pickup["kind"]
            if weapon in held or weapon not in self.DETOUR_DISTANCE:
                continue
            point = tuple(pickup["pos"][:2])
            distance = math.dist(position, point)
            if distance > self.DETOUR_DISTANCE[weapon] or math.dist(point, zone["center"]) > zone["radius"] - 9.0:
                continue
            if abs(pickup["pos"][2] - self.world.ground(*point)) > 2.8:
                continue
            detour_return = max(0.0, math.dist(point, zone["next_center"]) - next_radius)
            extra = max(0.0, distance + detour_return - current_return)
            seconds = 1.7 * (distance + detour_return) / me["stats"]["walk_speed"]
            if before_shrink < 5.0 + seconds or extra > self.DETOUR_DISTANCE[weapon] * 1.45:
                continue
            merit = distance + 0.5 * extra - 9.0 * self.PICKUP_PRIORITY[weapon]
            options.append((merit, pickup["id"], point))
        return min(options)[2] if options else None

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
                if distance <= 48.0 and abs(cube["pos"][2] - self.world.ground(*point)) < 3.0 and safely_inside(point):
                    viable_cubes.append((distance, point))
        if viable_cubes:
            return min(viable_cubes)[1], "cube"
        if me["weapons"][1] is None:
            weapons = []
            for pickup in obs["pickups"]:
                weapon = pickup["kind"]
                point = tuple(pickup["pos"][:2])
                if weapon not in self.PICKUP_PRIORITY or not safely_inside(point):
                    continue
                distance = math.dist(position, point)
                if distance <= 40.0 and abs(pickup["pos"][2] - self.world.ground(*point)) < 3.0:
                    weapons.append((distance - 4.0 * self.PICKUP_PRIORITY[weapon], point))
            if weapons:
                return min(weapons)[1], "weapon"
        return None

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
        enemy_target = self.select_enemy(me, enemies)
        self.combat_target = enemy_target["id"] if enemy_target else None
        threats = self.threat_sources(obs, position, enemies)
        health = me["hp"] / me["max_hp"]
        target_distance = math.dist(me["pos"], enemy_target["pos"]) if enemy_target else math.inf
        finish = bool(enemy_target and enemy_target.get("hp", 1400) < 350.0 and target_distance < 120.0 and health > 0.23)
        incoming = now - self.last_player_damage_at < 3.0
        crowded = len(threats) >= 2 and obs["alive"] > 4 and distance < 75.0
        reset = health < 0.60 and now - max(self.last_player_damage_at, self.hit_at) < 7.0
        retreat = bool(threats) and not finish and (health < 0.41 or reset or (incoming and crowded and health < 0.77))
        target = enemy_target
        if retreat and not urgent and math.dist(position, inside_goal) < 20.0:
            if now - self.cover_checked_at >= 0.8:
                self.cover_goal = self.cover_position(position, threats, next_center, next_radius)
                self.cover_checked_at = now
            if self.cover_goal is not None:
                goal = self.cover_goal
                mode = "cover"
        else:
            self.cover_goal = None
            if mode == "rotate":
                weapon = self.early_weapon_detour(obs, position, distance)
                if weapon is not None:
                    goal, mode = weapon, "early-weapon"
            if mode == "hold":
                if self.SEEK_RESOURCES and distance > 120.0 and not incoming:
                    resource = self.resource_goal(obs, position, enemies)
                    if resource is not None:
                        goal, mode = resource
                    else:
                        farm = self.farm_target(obs, distance)
                        if farm is not None:
                            target, mode = farm, "farm"
                if mode == "hold" and enemy_target:
                    mode = "fight"
                if mode in {"farm", "fight"}:
                    if now - self.combat_checked_at >= 0.55 or self.combat_goal is None:
                        self.combat_goal = self.attack_position(obs, target, next_center, next_radius, mode == "farm")
                        self.combat_checked_at = now
                    goal = self.combat_goal
                else:
                    self.combat_goal = None

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
            if mode in {"cube", "weapon", "early-weapon", "supply"} and len(self.waypoints) <= 1 and math.dist(position, goal) < 8.0:
                body_height = me["stats"]["height"] / 2.0
                if not self.world.blocked_between(
                    (*position, me["pos"][2] + body_height), (*goal, self.world.ground(*goal) + body_height)
                ):
                    waypoint = goal
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

        if target:
            aim, fire = weapon_plan(me, target, self.world, self.config, retreat=retreat or urgent)
            action["aim"] = list(aim)
            if finish or not retreat or incoming or target_distance < 75.0:
                action["fire"] = fire
        elif self.last_damage_bearing is not None and now - self.last_damage_at < 2.0:
            action["aim"] = [self.last_damage_bearing, 0.0]

        pickup = self.take_weapon(obs)
        if pickup is not None:
            action["take"] = pickup

        gun = me["weapons"][0]
        if gun and gun["kind"] == "gun" and distance > 95.0 and not any(action.get("fire", ())):
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
        self.active_mode = mode
        return action


if __name__ == "__main__":
    mf.run(Bot)
