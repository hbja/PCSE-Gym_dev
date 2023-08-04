import unittest
from stable_baselines3.common.vec_env import VecNormalize, DummyVecEnv
import initialize_env as init_env
from pcse_gym.utils.eval import FindOptimum


class TestCeres(unittest.TestCase):
    def setUp(self):
        self.env = init_env.initialize_env(pcse_env=0, crop_features=init_env.get_crop_features(pcse_env=0))
        self.env = VecNormalize(DummyVecEnv([lambda: self.env]), norm_reward=True, clip_reward=50., gamma=1)

    def test_single_year(self):
        ceres_result = FindOptimum(self.env, [1992]).optimize_start_dump().item()
        self.assertAlmostEqual(17.6, ceres_result, 1)

    def test_multiple_years(self):
        ceres_result = FindOptimum(self.env, [1992, 2002]).optimize_start_dump().item()
        self.assertAlmostEqual(19.1, ceres_result, 1)


