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


class GroundMap:
    GRID = 6.0
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
        for prism in world["prisms"]:
            shape = Polygon(prism["ring"])
            holes = [Polygon(ring) for ring in prism["holes"]]
            min_x, max_x, min_y, max_y = shape.bounds
            first_x, first_y = self.cell((min_x - self.radius, min_y - self.radius))
            last_x, last_y = self.cell((max_x + self.radius, max_y + self.radius))
            for x in range(max(0, first_x), min(self.width, last_x + 1)):
                for y in range(max(0, first_y), min(self.depth, last_y + 1)):
                    cell = (x, y)
                    ground = self.elevation[cell]
                    if prism["base"] >= ground + stats["height"] or prism["top"] <= ground + stats["step_up"]:
                        continue
                    point = self.points[cell]
                    if shape.intersects_disc(point, self.radius) and not any(
                        hole.contains_disc(point, self.radius) for hole in holes
                    ):
                        blocked.add(cell)
        self.open = self.points.keys() - blocked
        self.edges = {cell: tuple(self.walk_neighbors(cell)) for cell in self.open}

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
    name = "ground-route-v0"
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

    def act(self, obs):
        me, zone, now = obs["self"], obs["zone"], obs["t"]
        position = tuple(me["pos"][:2])
        next_radius = max(0.0, zone["next_radius"] - min(18.0, zone["next_radius"] / 4.0))
        next_center = tuple(zone["next_center"])
        inside_goal = within_radius(position, next_center, next_radius)
        urgent = not me["in_zone"]
        goal = inside_goal
        if urgent:
            goal = within_radius(position, tuple(zone["center"]), max(0.0, zone["radius"] - 9.0))

        enemies = [enemy for enemy in obs["mechs"] if enemy["kind"] == "mech"]
        nearest = min(enemies, key=lambda enemy: math.dist(me["pos"], enemy["pos"]), default=None)
        distance = math.dist(me["pos"], nearest["pos"]) if nearest else math.inf
        retreat = distance < 35.0 or (distance < 85.0 and me["hp"] < me["max_hp"] * 0.8)
        if retreat and not urgent and math.dist(position, inside_goal) < 20.0:
            dx, dy = unit_toward(nearest["pos"][:2], position)
            escape = position[0] + 35.0 * dx, position[1] + 35.0 * dy
            goal = within_radius(escape, next_center, next_radius)

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
        boost = (urgent or retreat) and me["energy"] > 32.0 and math.hypot(*move) > 0.8
        yaw = math.atan2(move[1], move[0]) if math.hypot(*move) > 0.1 else me["aim"][0]
        action = {"move": list(move), "boost": boost, "aim": [yaw, 0.0], "jump": recovering and me["grounded"]}

        if nearest and distance < 120.0:
            gun = me["weapons"][0]
            speed = gun["stats"].get("speed", 252.0) if gun else 252.0
            flight = distance / speed
            dx, dy, dz = [
                target + velocity * flight - origin
                for target, velocity, origin in zip(nearest["pos"], nearest["vel"], me["pos"])
            ]
            yaw = math.atan2(dy, dx)
            pitch = math.atan2(dz - me["stats"]["muzzle_height"] + 4.5, math.hypot(dx, dy))
            pitch = min(me["stats"]["max_pitch"], max(me["stats"]["min_pitch"], pitch))
            action["aim"] = [yaw, pitch]
            aimed = abs(math.remainder(yaw - me["aim"][0], 2 * math.pi)) < 0.075
            aimed = aimed and abs(pitch - me["aim"][1]) < 0.09
            action["fire"] = [aimed and not urgent and (not retreat or distance < 20.0), False]

        if obs["offer"]:
            choices = obs["offer"]["choices"]
            action["pick"] = min(
                range(len(choices)),
                key=lambda i: self.UPGRADE_PRIORITY.get(choices[i]["id"], len(self.UPGRADE_PRIORITY)),
            )
        return action


if __name__ == "__main__":
    mf.run(Bot)
