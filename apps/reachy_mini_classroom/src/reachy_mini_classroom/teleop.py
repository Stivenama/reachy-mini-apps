"""Manual view offsets composed by the Hub's sole motion writer."""
import math
import threading
import time


LIMITS = (25.0, 20.0, 45.0)  # head yaw relative to body, pitch, body yaw
LEASE_SECONDS = 1.2


class ViewControl:
    def __init__(self, clock=time.monotonic, prepare=None):
        self.clock = clock
        self.prepare = prepare
        self.lock = threading.RLock()
        self.owner = None
        self.deadline = 0.0
        self.sequence = -1
        self.target = [0.0] * 3
        self.current = [0.0] * 3
        self.velocity = [0.0] * 3
        self.natural = True
        self.error = ''
        self.output_body = 0.0

    def _expire(self):
        if self.owner and self.clock() > self.deadline:
            self.owner = None
            self.target = self.current.copy()
            self.velocity = [0.0] * 3

    def claim(self, owner):
        with self.lock:
            self._expire()
            if self.owner and self.owner != owner:
                raise ValueError('Otro dispositivo está controlando a Reachy.')
            if self.owner != owner:
                if self.prepare:
                    self.prepare()
                self.sequence = -1
            self.owner = owner
            self.deadline = self.clock() + LEASE_SECONDS
            return self.status()

    def command(self, owner, sequence, yaw, pitch, body, natural):
        values = (yaw, pitch, body)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError('La posición debe contener números finitos.')
        if any(abs(v) > limit for v, limit in zip(values, LIMITS)):
            raise ValueError('Posición fuera de los límites de la cámara.')
        with self.lock:
            self._expire()
            if self.owner != owner:
                raise ValueError('Control desconectado. Pulsa «Controlar Reachy» de nuevo.')
            if type(sequence) is not int or sequence <= self.sequence:
                raise ValueError('Orden de movimiento antigua.')
            # A conservative combined envelope leaves room for Hub breathing/wobble.
            norm = max(1.0, math.hypot(yaw / LIMITS[0], pitch / LIMITS[1]))
            self.target = [yaw / norm, pitch / norm, body]
            self.natural = bool(natural)
            self.sequence = sequence
            self.deadline = self.clock() + LEASE_SECONDS

    def release(self, owner):
        with self.lock:
            if self.owner == owner:
                self.owner = None
                self.target = self.current.copy()
                self.velocity = [0.0] * 3

    def step(self, dt):
        with self.lock:
            self._expire()
            dt = min(.05, max(0.0, dt))
            for i, target in enumerate(self.target):
                error = target - self.current[i]
                speed = math.copysign(min(22.0, abs(error) / .22, math.sqrt(120 * abs(error))), error)
                self.velocity[i] += max(-60 * dt, min(60 * dt, speed - self.velocity[i]))
                delta = self.velocity[i] * dt
                if delta * error >= 0 and abs(delta) >= abs(error):
                    self.current[i], self.velocity[i] = target, 0.0
                else:
                    self.current[i] += delta
                self.current[i] = max(-LIMITS[i], min(LIMITS[i], self.current[i]))
            return self.current.copy(), self.natural

    def status(self):
        with self.lock:
            self._expire()
            return dict(claimed=self.owner is not None, angles=self.current.copy(),
                        target=self.target.copy(), natural=self.natural, error=self.error,
                        limits=list(LIMITS), output_body_radians=self.output_body)


class MotionOutput:
    """Proxy: keep exactly one set_target call per Hub tick, including antennas."""
    def __init__(self, robot, control):
        self.robot, self.control = robot, control
        self.last_tick = time.monotonic()
        self.natural_blend = 0.0

    def __getattr__(self, name):
        return getattr(self.robot, name)

    def set_target(self, head, antennas, body_yaw):
        from reachy_mini.utils import create_head_pose
        now = time.monotonic()
        dt = min(.05, max(0.0, now - self.last_tick))
        (yaw, pitch, body), natural = self.control.step(dt)
        self.last_tick = now
        self.natural_blend += max(-dt * 1.5, min(dt * 1.5, float(natural) - self.natural_blend))
        # Rotate in the base frame, then retain the Hub's small local breathing pose.
        offset = create_head_pose(yaw=yaw + body, pitch=pitch, degrees=True)
        pose = offset @ head
        antenna_values = list(antennas)
        antenna_values[0] += self.natural_blend * math.radians(5) * math.sin(now * 1.7)
        antenna_values[1] += self.natural_blend * math.radians(5) * math.sin(now * 1.4 + .8)
        try:
            self.control.output_body = body_yaw + math.radians(body)
            self.robot.set_target(head=pose, antennas=antenna_values,
                                  body_yaw=body_yaw + math.radians(body))
            self.control.error = ''
        except Exception:
            with self.control.lock:
                self.control.release(self.control.owner)
                self.control.error = 'El robot no respondió al movimiento. Revisa su conexión.'
            raise
