from __future__ import annotations

import math
import time

import pygame


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class GyroMap:
    """Circular simulated map driven by fused heading and linear acceleration."""

    def __init__(self) -> None:
        self.active = False
        self.buttons: list[tuple[str, pygame.Rect, object]] = []
        self.position = [96.0, 72.0]
        self.velocity = [0.0, 0.0]
        self.heading = 0.0
        self.gravity = (0.0, 0.0, 1.0)
        self.last_update = time.monotonic()

    def open(self, accel: tuple[float, float, float]) -> None:
        self.active = True
        self.gravity = accel
        self.velocity = [0.0, 0.0]
        self.last_update = time.monotonic()

    def close(self) -> None:
        self.active = False
        self.velocity = [0.0, 0.0]

    def handle_click(self, position: tuple[int, int]) -> bool:
        for _, rect, action in self.buttons:
            if rect.collidepoint(position):
                action()
                return True
        return False

    def update(
        self,
        imu: tuple[float, ...] | None,
        heading: float | None,
        now: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        dt = clamp(now - self.last_update, 0.0, 0.05)
        self.last_update = now
        if heading is not None:
            self.heading = heading % 360.0
        if not self.active or not imu or dt <= 0.0:
            return

        ax, ay, az = imu[:3]
        gyro_x, gyro_y, gyro_z = (math.radians(value) for value in imu[3:6])
        gravity_x, gravity_y, gravity_z = self.gravity

        cross_x = gyro_y * gravity_z - gyro_z * gravity_y
        cross_y = gyro_z * gravity_x - gyro_x * gravity_z
        cross_z = gyro_x * gravity_y - gyro_y * gravity_x
        gravity_x -= cross_x * dt
        gravity_y -= cross_y * dt
        gravity_z -= cross_z * dt

        accel_norm = math.sqrt(ax * ax + ay * ay + az * az)
        if 0.82 <= accel_norm <= 1.18:
            correction = 1.0 - math.exp(-1.8 * dt)
            gravity_x += (ax - gravity_x) * correction
            gravity_y += (ay - gravity_y) * correction
            gravity_z += (az - gravity_z) * correction
        gravity_norm = math.sqrt(gravity_x * gravity_x + gravity_y * gravity_y + gravity_z * gravity_z)
        if gravity_norm > 0.2:
            gravity_x /= gravity_norm
            gravity_y /= gravity_norm
            gravity_z /= gravity_norm
        self.gravity = (gravity_x, gravity_y, gravity_z)

        def linear_axis(value: float) -> float:
            deadzone = 0.028
            if abs(value) <= deadzone:
                return 0.0
            return math.copysign(min(0.65, abs(value) - deadzone), value)

        local_right = linear_axis(-(ax - gravity_x))
        local_forward = linear_axis(ay - gravity_y)
        heading_rad = math.radians(self.heading)
        accel_east = (local_right * math.cos(heading_rad) + local_forward * math.sin(heading_rad)) * 520.0
        accel_north = (local_forward * math.cos(heading_rad) - local_right * math.sin(heading_rad)) * 520.0
        self.velocity[0] += accel_east * dt
        self.velocity[1] += accel_north * dt
        damping = math.exp(-2.8 * dt)
        self.velocity[0] = clamp(self.velocity[0] * damping, -150.0, 150.0)
        self.velocity[1] = clamp(self.velocity[1] * damping, -150.0, 150.0)
        self.position[0] += self.velocity[0] * dt
        self.position[1] += self.velocity[1] * dt

    def draw(
        self,
        screen: pygame.Surface,
        title_font: pygame.font.Font,
        body_font: pygame.font.Font,
        label_font: pygame.font.Font,
    ) -> None:
        width, height = screen.get_size()
        self.buttons = []
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((226, 230, 222, 232))
        screen.blit(overlay, (0, 0))

        radius = max(120, min((width - 48) // 2, (height - 150) // 2))
        size = radius * 2
        surface = pygame.Surface((size, size), pygame.SRCALPHA)
        surface.fill((232, 174, 58, 255))
        heading_rad = math.radians(self.heading)
        cos_h, sin_h = math.cos(heading_rad), math.sin(heading_rad)
        pos_east, pos_north = self.position

        def project(east: float, north: float) -> tuple[int, int]:
            dx, dy = east - pos_east, north - pos_north
            return (
                round(radius + dx * cos_h - dy * sin_h),
                round(radius - dx * sin_h - dy * cos_h),
            )

        grid = 132.0
        east_cell = math.floor(pos_east / grid)
        north_cell = math.floor(pos_north / grid)
        building_colors = ((181, 111, 24), (194, 123, 27), (166, 94, 22), (205, 137, 33))
        for gx in range(east_cell - 3, east_cell + 4):
            for gy in range(north_cell - 3, north_cell + 4):
                inset_x = 19 + abs((gx * 7 + gy * 3) % 10)
                inset_y = 18 + abs((gx * 5 - gy * 2) % 11)
                x0, x1 = gx * grid + inset_x, (gx + 1) * grid - inset_x
                y0, y1 = gy * grid + inset_y, (gy + 1) * grid - inset_y
                polygon = [project(x0, y0), project(x1, y0), project(x1, y1), project(x0, y1)]
                color = building_colors[(gx * 3 + gy * 5) % len(building_colors)]
                pygame.draw.polygon(surface, color, polygon)
                pygame.draw.polygon(surface, (213, 149, 43), polygon, 2)
                if (gx + gy) % 3 == 0:
                    divider_a = project((x0 + x1) * 0.5, y0)
                    divider_b = project((x0 + x1) * 0.5, y1)
                    pygame.draw.line(surface, (143, 79, 20), divider_a, divider_b, 3)

        road = (250, 235, 188)
        road_edge = (218, 158, 48)
        span = radius * 2.5
        for gx in range(east_cell - 3, east_cell + 4):
            p1, p2 = project(gx * grid, pos_north - span), project(gx * grid, pos_north + span)
            road_width = 30 if gx % 2 == 0 else 22
            pygame.draw.line(surface, road_edge, p1, p2, road_width + 5)
            pygame.draw.line(surface, road, p1, p2, road_width)
        for gy in range(north_cell - 3, north_cell + 4):
            p1, p2 = project(pos_east - span, gy * grid), project(pos_east + span, gy * grid)
            road_width = 30 if gy % 2 else 22
            pygame.draw.line(surface, road_edge, p1, p2, road_width + 5)
            pygame.draw.line(surface, road, p1, p2, road_width)

        curve_offset = round((pos_north - (0.42 * pos_east + 58.0 * math.sin(pos_east / 155.0))) / 420.0) * 420.0
        highway = []
        for east in range(round(pos_east) - radius * 3, round(pos_east) + radius * 3 + 1, 18):
            north = 0.42 * east + 58.0 * math.sin(east / 155.0) + curve_offset
            highway.append(project(float(east), north))
        if len(highway) > 1:
            pygame.draw.lines(surface, (211, 145, 40), False, highway, 43)
            pygame.draw.lines(surface, road, False, highway, 35)

        coast_x = round((pos_east - 165.0) / 620.0) * 620.0 + 165.0
        coast_world = []
        for north in range(round(pos_north) - radius * 3, round(pos_north) + radius * 3 + 1, 16):
            east = coast_x + 24.0 * math.sin(north / 82.0) + 11.0 * math.sin(north / 29.0)
            coast_world.append((east, float(north)))
        coast = [project(east, north) for east, north in coast_world]
        water = coast + [
            project(coast_x + span * 2.0, coast_world[-1][1]),
            project(coast_x + span * 2.0, coast_world[0][1]),
        ]
        pygame.draw.polygon(surface, (139, 188, 169), water)
        pygame.draw.lines(surface, (77, 75, 45), False, coast, 13)
        pygame.draw.lines(surface, (229, 178, 59), False, coast, 5)

        nearest_east = round(pos_east / grid) * grid
        nearest_north = round(pos_north / grid) * grid
        police = project(nearest_east - grid * 0.58, nearest_north + grid * 0.72)
        outer_star = self._star(police, 15, 8)
        inner_star = self._star(police, 11, 6)
        pygame.draw.polygon(surface, (40, 34, 23), outer_star)
        pygame.draw.polygon(surface, (250, 213, 83), inner_star)

        label = pygame.Surface((104, 35), pygame.SRCALPHA)
        label.blit(label_font.render("VICE CITY", True, (42, 31, 18)), (0, 1))
        label.blit(label_font.render("POLICE DEPT", True, (42, 31, 18)), (0, 16))
        label = pygame.transform.rotate(label, self.heading)
        label_center = project(nearest_east - grid * 0.05, nearest_north + grid * 0.74)
        surface.blit(label, label.get_rect(center=label_center))

        mask = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(mask, (255, 255, 255, 255), (radius, radius), radius)
        surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        map_center = (width // 2, 32 + radius)
        screen.blit(surface, surface.get_rect(center=map_center))
        pygame.draw.circle(screen, (2, 3, 3), map_center, radius, 16)
        pygame.draw.circle(screen, (12, 13, 12), map_center, radius - 9, 2)

        north = (
            round(map_center[0] - math.sin(heading_rad) * (radius - 8)),
            round(map_center[1] - math.cos(heading_rad) * (radius - 8)),
        )
        pygame.draw.circle(screen, (2, 3, 3), north, 25)
        north_glyph = title_font.render("N", True, (250, 250, 246))
        screen.blit(north_glyph, north_glyph.get_rect(center=north))

        px, py = map_center
        pygame.draw.polygon(screen, (4, 5, 5), [(px, py - 24), (px - 17, py + 3), (px + 17, py + 3)])
        pygame.draw.circle(screen, (4, 5, 5), (px, py + 4), 17)
        pygame.draw.polygon(screen, (250, 250, 247), [(px, py - 16), (px - 10, py + 2), (px + 10, py + 2)])
        pygame.draw.circle(screen, (250, 250, 247), (px, py + 4), 10)

        close_rect = pygame.Rect(width // 2 - 60, height - 78, 120, 36)
        pygame.draw.rect(screen, (31, 40, 51), close_rect, border_radius=8)
        pygame.draw.rect(screen, (62, 76, 92), close_rect, 1, border_radius=8)
        close_glyph = body_font.render("Close", True, (238, 242, 247))
        screen.blit(close_glyph, close_glyph.get_rect(center=close_rect.center))
        self.buttons.append(("Close", close_rect, self.close))

    @staticmethod
    def _star(center: tuple[int, int], outer: float, inner: float) -> list[tuple[float, float]]:
        points = []
        for index in range(10):
            angle = math.radians(index * 36.0 - 90.0)
            radius = outer if index % 2 == 0 else inner
            points.append((center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius))
        return points
