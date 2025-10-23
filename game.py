import struct
import sys
import math
import pygame
import asyncio
import websockets
import socket
import threading
import json


WIDTH, HEIGHT = 800, 600
BG_COLOR = (30, 30, 30)
CIRCLE_COLOR = (100, 200, 255)
FPS = 60
UPDATE_RATE = 100
WS_URI = "ws://127.0.0.1:8888/ws"

class Circle:
    def __init__(self, x, y, r=20):
        self.x = x
        self.y = y
        self.r = r
        self.vx = 0.0
        self.vy = 0.0
        # Smooth interpolation state (for other players)
        self._smooth_start_x = x
        self._smooth_start_y = y
        self._smooth_target_x = x
        self._smooth_target_y = y
        self._smooth_progress = 1.0  # 1.0 means already at target
        self._smooth_duration = 0.05  # 50ms total blend time

    def move_to_position(self, x, y):
        self.target_x = x
        self.target_y = y
        self.interpolation_progress = 0.0

    def set_position(self, x, y):
        """Schedule a smooth move to (x, y) over self._smooth_duration seconds.
        Used for other circles updated via network.
        """
        # Start from current rendered position
        self._smooth_start_x = self.x
        self._smooth_start_y = self.y
        self._smooth_target_x = float(x)
        self._smooth_target_y = float(y)
        self._smooth_progress = 0.0

    def update(self, dt, desired_vx=None, desired_vy=None, smooth=False):
        # if desired velocity provided, set velocity directly (non-slippery)
        if not smooth:
            if desired_vx is not None and desired_vy is not None:
                self.vx = desired_vx
                self.vy = desired_vy
            # position update (main/local circle)
            self.x += self.vx * dt
            self.y += self.vy * dt
        else:
            # Smoothly interpolate for remote/other circles
            if self._smooth_progress < 1.0:
                if self._smooth_duration <= 0:
                    t = 1.0
                else:
                    self._smooth_progress += dt / self._smooth_duration
                    if self._smooth_progress > 1.0:
                        self._smooth_progress = 1.0
                    t = self._smooth_progress
                # Linear interpolation (can swap for ease-in/out if desired)
                self.x = self._smooth_start_x + (self._smooth_target_x - self._smooth_start_x) * t
                self.y = self._smooth_start_y + (self._smooth_target_y - self._smooth_start_y) * t

        # keep inside window (clamp)
        self.x = max(self.r, min(WIDTH - self.r, self.x))
        self.y = max(self.r, min(HEIGHT - self.r, self.y))

    def draw(self, surf):
        pygame.draw.circle(surf, CIRCLE_COLOR, (int(self.x), int(self.y)), self.r)


def run():
    # --- Minimal background network thread: WS + UDP ---
    stop_event = threading.Event()

    def net_thread():
        async def net_main():
            loop = asyncio.get_running_loop()

            # Create UDP socket once
            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_sock.bind(("127.0.0.1", 0))
            udp_sock.setblocking(False)
            remote_host = None
            remote_port = None
            print(f"[UDP] listening on {udp_sock.getsockname()[0]}:{udp_sock.getsockname()[1]}")

            async def udp_sender(sock):
                while not stop_event.is_set():
                    try:
                        if remote_host is not None and remote_port is not None:
                            await loop.sock_sendto(sock, struct.pack(">II", int(circle.x), int(circle.y)), (remote_host, remote_port))
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        if not stop_event.is_set():  # Only log if not shutting down
                            print(f"[UDP send] error: {e}")
                        break

            async def udp_receiver(sock):
                while not stop_event.is_set():
                    try:
                        data, addr = await asyncio.wait_for(
                            loop.sock_recvfrom(sock, 2048), timeout=0.1
                        )
                        if isinstance(data, (bytes, bytearray)) and len(data) % 8 == 0:
                            positions = struct.unpack(">" + "II" * (len(data) // 8), data)
                            for i in range(0, len(positions), 2):
                                x = positions[i]
                                y = positions[i + 1]
                                idx = i // 2
                                if 0 <= idx < len(other_circles):
                                    other_circles[idx].set_position(x, y)
                    except asyncio.TimeoutError:
                        pass
                    except Exception as e:
                        if not stop_event.is_set():  # Only log if not shutting down
                            print(f"[UDP recv] error: {e}")
                        break

            async def ws_loop():
                nonlocal remote_host, remote_port  # Allow modifying outer scope variables
                while not stop_event.is_set():
                    try:
                        async with websockets.connect(WS_URI) as ws:
                            async for message in ws:
                                data = json.loads(message)
                                if data.get("event") == "udp_request":
                                    remote_host = data.get("ip", None)
                                    remote_port = data.get("port", None)
                                    await ws.send(json.dumps({"event": "udp_response", "ip": udp_sock.getsockname()[0], "port": udp_sock.getsockname()[1]}))
                                elif data.get("event") == "user_disconnected":
                                    other_circles.pop(data.get("user_index", None))
                                elif data.get("event") == "new_connection":
                                    other_circles.append(Circle(0, 0, r=24))
                                elif data.get("event") == "connection_information":
                                    num_clients = data.get("number_of_clients", 0)
                                    for _ in range(num_clients):
                                        other_circles.append(Circle(0, 0, r=24))
                    except Exception as e:
                        print(f"[WS] error: {e}")
                        await asyncio.sleep(1.0)

            try:
                await asyncio.gather(ws_loop(), udp_sender(udp_sock), udp_receiver(udp_sock))
            finally:
                udp_sock.close()

        try:
            asyncio.run(net_main())
        except Exception as e:
            print(f"[NET] thread exit: {e}")

    t = threading.Thread(target=net_thread, name="net-thread", daemon=True)
    t.start()

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Moving Circle - Non-slippery")
    clock = pygame.time.Clock()
    circle = Circle(0, 0, r=24)
    other_circles = []
    speed = 300.0  # pixels per second (instant velocity when key held)

    running = True
    while running:
        dt_ms = clock.tick(FPS)
        dt = dt_ms / 1000.0

        # input -> desired velocity
        keys = pygame.key.get_pressed()
        dir_x = 0
        dir_y = 0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            dir_x -= 1
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            dir_x += 1
        if keys[pygame.K_UP] or keys[pygame.K_w]:
            dir_y -= 1
        if keys[pygame.K_DOWN] or keys[pygame.K_s]:
            dir_y += 1

        # normalize to avoid faster diagonal movement
        if dir_x != 0 or dir_y != 0:
            mag = math.hypot(dir_x, dir_y)
            dir_x /= mag
            dir_y /= mag
            desired_vx = dir_x * speed
            desired_vy = dir_y * speed
        else:
            desired_vx = 0.0
            desired_vy = 0.0

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        circle.update(dt, desired_vx=desired_vx, desired_vy=desired_vy)
        # advance smooth interpolation for other circles
        for oc in other_circles:
            oc.update(dt, smooth=True)

        screen.fill(BG_COLOR)
        circle.draw(screen)
        for oc in other_circles:
            oc.draw(screen)
        pygame.display.flip()

    pygame.quit()
    # stop background thread
    stop_event.set()
    t.join(timeout=1.5)
    sys.exit()

if __name__ == "__main__":
    run()