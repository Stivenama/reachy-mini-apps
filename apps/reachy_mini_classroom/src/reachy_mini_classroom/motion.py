"""Use the installed Hub's movement manager and audio-reactive head motion."""
class HubMotion:
    def __init__(self, robot):
        from reachy_mini_hub.moves import MovementManager
        from .teleop import MotionOutput, ViewControl
        self.robot = robot
        self.view = ViewControl(prepare=self.prepare_view)
        self.manager = MovementManager(current_robot=MotionOutput(robot, self.view))
        self.robot.set_automatic_body_yaw(False)
        self.manager.start()
        self.robot.enable_wobbling()

    def listening(self, active): self.manager.set_listening(active)
    def speaking(self, active): self.manager.set_speaking(active)

    def prepare_view(self):
        # Other apps can leave the daemon's global tracking / auto-yaw enabled.
        self.robot.stop_head_tracking()
        self.robot.set_automatic_body_yaw(False)

    def close(self):
        self.manager.stop(reset_to_neutral=False)
        self.robot.disable_wobbling()
        self.robot.set_automatic_body_yaw(True)
