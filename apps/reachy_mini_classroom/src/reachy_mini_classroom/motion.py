"""Use the installed Hub's movement manager and audio-reactive head motion."""
class HubMotion:
    def __init__(self, robot):
        from reachy_mini_hub.moves import MovementManager
        self.robot = robot
        self.manager = MovementManager(current_robot=robot)
        self.manager.start()
        self.robot.enable_wobbling()

    def listening(self, active): self.manager.set_listening(active)
    def speaking(self, active): self.manager.set_speaking(active)

    def close(self):
        self.manager.stop(reset_to_neutral=False)
        self.robot.disable_wobbling()
