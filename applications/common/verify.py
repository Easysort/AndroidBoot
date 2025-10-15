from applications.common.env import Env

def test_env_initialization(): Env.DEVICE_ID is not None or len(Env.DEVICE_ID) < 1