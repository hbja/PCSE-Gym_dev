import argparse
import lib_programname
import sys
import os.path
import torch.nn as nn
from comet_ml import Experiment
from comet_ml.integration.gymnasium import CometLogger
import gymnasium.spaces
from stable_baselines3 import PPO, DQN
from stable_baselines3.common.vec_env import VecNormalize, DummyVecEnv
from stable_baselines3.common.monitor import Monitor
from sb3_contrib import RecurrentPPO  # MaskablePPO
# from sb3_contrib.common.envs import InvalidActionEnvDiscrete
# from sb3_contrib.common.maskable.utils import get_action_masks
# from sb3_contrib.common.maskable.policies import MaskableActorCriticPolicy
# from sb3_contrib.common.wrappers import ActionMasker

from pcse_gym.envs.constraints import ActionLimiter, VecNormalizePO
from pcse_gym.envs.winterwheat import WinterWheat
from pcse_gym.envs.sb3 import get_policy_kwargs, get_model_kwargs
from pcse_gym.utils.eval import EvalCallback, determine_and_log_optimum
import pcse_gym.utils.defaults as defaults

# from pcse_gym.envs.constraints import ActionLimiter

path_to_program = lib_programname.get_path_executed_script()
rootdir = path_to_program.parents[0]

if rootdir not in sys.path:
    print(f'insert {os.path.join(rootdir)}')
    sys.path.insert(0, os.path.join(rootdir))

if os.path.join(rootdir, "pcse_gym") not in sys.path:
    sys.path.insert(0, os.path.join(rootdir, "pcse_gym"))


def wrapper_vectorized_env(env_pcse_train, flag_po):
    if flag_po:
        return VecNormalizePO(DummyVecEnv([lambda: env_pcse_train]), norm_obs=True, norm_reward=True,
                              clip_obs=10., clip_reward=50., gamma=1)
    else:
        return VecNormalize(DummyVecEnv([lambda: env_pcse_train]), norm_obs=True, norm_reward=True,
                            clip_obs=10., clip_reward=50., gamma=1)


def train(log_dir, n_steps,
          crop_features=defaults.get_wofost_default_crop_features(),
          weather_features=defaults.get_default_weather_features(),
          action_features=defaults.get_default_action_features(),
          train_years=defaults.get_default_train_years(),
          test_years=defaults.get_default_test_years(),
          train_locations=defaults.get_default_location(),
          test_locations=defaults.get_default_location(),
          action_space=defaults.get_default_action_space(),
          pcse_model=0, agent=PPO, reward=None,
          seed=0, tag="Exp", costs_nitrogen=10.0,
          **kwargs):
    """
    Train a PPO agent (Stable Baselines3).

    Parameters
    ----------
    log_dir: directory where the (tensorboard) data will be saved
    n_steps: int, number of timesteps the agent spends in the environment
    crop_features: crop features
    weather_features: weather features
    action_features: action features
    train_years: train years
    test_years: test years
    train_locations: train locations (latitude,longitude)
    test_locations: test locations (latitude,longitude)
    action_space: action space
    pcse_model: 0 for LINTUL else WOFOST
    agent: one of {PPO, RPPO, DQN}
    reward: one of {DEF, GRO, or ANE}
    seed: random seed
    tag: tag for tensorboard and friends
    costs_nitrogen: float, penalty for fertilization application

    """
    if agent == 'DQN':
        assert not kwargs.get('args_measure'), f'cannot use {agent} with measure args'

    pcse_model_name = "LINTUL" if not pcse_model else "WOFOST"

    flag_limit_action = kwargs.get('limit_action')
    flag_po = kwargs.get('po_features')

    print(f'Train model {pcse_model_name} with {agent} algorithm and seed {seed}. Logdir: {log_dir}')
    if agent == 'PPO' or 'RPPO':
        hyperparams = {'batch_size': 64, 'n_steps': 2048, 'learning_rate': 0.0003, 'ent_coef': 0.0, 'clip_range': 0.3,
                       'n_epochs': 10, 'gae_lambda': 0.95, 'max_grad_norm': 0.5, 'vf_coef': 0.5,
                       'policy_kwargs': get_policy_kwargs(n_crop_features=len(crop_features),
                                                          n_weather_features=len(weather_features),
                                                          n_action_features=len(action_features))}
        hyperparams['policy_kwargs']['net_arch'] = dict(pi=[256, 256], vf=[256, 256])
        hyperparams['policy_kwargs']['activation_fn'] = nn.Tanh
        hyperparams['policy_kwargs']['ortho_init'] = False
    if agent == 'DQN':
        hyperparams = {'exploration_fraction': 0.1, 'exploration_initial_eps': 1.0, 'exploration_final_eps': 0.01,
                       'policy_kwargs': get_policy_kwargs(n_crop_features=len(crop_features),
                                                          n_weather_features=len(weather_features),
                                                          n_action_features=len(action_features))
                       }
        hyperparams['policy_kwargs']['net_arch'] = [256, 256]
        hyperparams['policy_kwargs']['activation_fn'] = nn.Tanh

    # For comet use
    use_comet = True

    if use_comet:
        with open(os.path.join(rootdir, 'comet', 'hbja_api_key'), 'r') as f:
            api_key = f.readline()
        comet_log = Experiment(
            api_key=api_key,
            project_name="experimental_cropgym",
            workspace="pcse-gym",
            log_graph=True,
            auto_metric_logging=True,
            auto_histogram_tensorboard_logging=True
        )

        comet_log.log_parameters(hyperparams)
        comet_log.log_asset_folder(os.path.join(rootdir, 'pcse_gym', 'envs'), log_file_name=True, recursive=True)
        comet_log.log_asset(os.path.join(rootdir, 'pcse_gym', 'utils', 'eval'), file_name='eval.py')

    env_pcse_train = WinterWheat(crop_features=crop_features, action_features=action_features,
                                 weather_features=weather_features,
                                 costs_nitrogen=costs_nitrogen, years=train_years, locations=train_locations,
                                 action_space=action_space, action_multiplier=1.0, seed=seed,
                                 reward=reward, **get_model_kwargs(pcse_model, train_locations), **kwargs)

    if flag_limit_action:
        env_pcse_train = ActionLimiter(env_pcse_train, action_limit=4)

    # env_pcse_train = ActionMasker(env_pcse_train, mask_fertilization_actions)

    env_pcse_train = Monitor(env_pcse_train)

    # if use_comet and comet_log:
    #     env_pcse_train = CometLogger(env_pcse_train, comet_log)

    match agent:
        case 'PPO':
            env_pcse_train = wrapper_vectorized_env(env_pcse_train, flag_po)
            model = PPO('MlpPolicy', env_pcse_train, gamma=1, seed=seed, verbose=0, **hyperparams,
                        tensorboard_log=log_dir)
        case 'DQN':
            env_pcse_train = VecNormalize(DummyVecEnv([lambda: env_pcse_train]), norm_obs=True, norm_reward=True,
                                          clip_obs=10000., clip_reward=5000., gamma=1)
            model = DQN('MlpPolicy', env_pcse_train, gamma=1, seed=seed, verbose=0, **hyperparams,
                        tensorboard_log=log_dir)
        case 'RPPO':
            env_pcse_train = wrapper_vectorized_env(env_pcse_train, flag_po)
            model = RecurrentPPO('MlpLstmPolicy', env_pcse_train, gamma=1, seed=seed, verbose=0, **hyperparams,
                                 tensorboard_log=log_dir)
        case _:
            env_pcse_train = wrapper_vectorized_env(env_pcse_train, flag_po)
            model = PPO('MlpPolicy', env_pcse_train, gamma=1, seed=seed, verbose=0, **hyperparams,
                        tensorboard_log=log_dir)

    compute_baselines = False
    if compute_baselines:
        determine_and_log_optimum(log_dir, env_pcse_train,
                                  train_years=train_years, test_years=test_years,
                                  train_locations=train_locations, test_locations=test_locations,
                                  n_steps=args.nsteps)

    env_pcse_eval = WinterWheat(crop_features=crop_features, action_features=action_features,
                                weather_features=weather_features,
                                costs_nitrogen=costs_nitrogen, years=test_years, locations=test_locations,
                                action_space=action_space, action_multiplier=1.0, reward=reward,
                                **get_model_kwargs(pcse_model, train_locations), **kwargs, seed=seed)
    if flag_limit_action:
        env_pcse_eval = ActionLimiter(env_pcse_eval, action_limit=4)

    tb_log_name = f'{tag}-{pcse_model_name}-Ncosts-{costs_nitrogen}-run'

    model.learn(total_timesteps=n_steps,
                callback=EvalCallback(env_eval=env_pcse_eval, test_years=test_years,
                                      train_years=train_years, train_locations=train_locations,
                                      test_locations=test_locations, seed=seed, pcse_model=pcse_model,
                                      **kwargs),
                tb_log_name=tb_log_name)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--seed", type=int, default=0, help="Set seed")
    parser.add_argument("-n", "--nsteps", type=int, default=400000, help="Number of steps")
    parser.add_argument("-c", "--costs_nitrogen", type=float, default=10.0, help="Costs for nitrogen")
    parser.add_argument("-e", "--environment", type=int, default=1,
                        help="Crop growth model. 0 for LINTUL-3, 1 for WOFOST")
    parser.add_argument("-a", "--agent", type=str, default="PPO", help="RL agent. PPO, RPPO, or DQN.")
    parser.add_argument("-r", "--reward", type=str, default="DEF", help="Reward function. DEF, DEP, GRO, or ANE")
    parser.add_argument("-m", "--measure", action='store_true', help="--measure or --no-measure."
                                                                     "Train an agent in a partially"
                                                                     "observable environment that"
                                                                     "decides when to measure"
                                                                     "certain crop features")
    parser.add_argument("--no-measure", action='store_false', dest='measure')
    parser.add_argument("--variable-recovery-rate", action='store_true', dest='vrr')
    parser.add_argument("--limit-freq", action='store_true', dest='limit_action')
    parser.set_defaults(measure=True, vrr=False, limit_action=False)

    args = parser.parse_args()
    if args.reward == 'DEP':
        args.vrr = True
    pcse_model_name = "LINTUL" if not args.environment else "WOFOST"
    print(rootdir)
    log_dir = os.path.join(rootdir, 'tensorboard_logs', f'{pcse_model_name}_experiments')
    print(f'train for {args.nsteps} steps with costs_nitrogen={args.costs_nitrogen} (seed={args.seed})')

    all_years = [*range(1990, 2022)]
    train_years = [year for year in all_years if year % 2 == 1]
    test_years = [year for year in all_years if year % 2 == 0]

    """The Netherlands"""
    # train_locations = [(52, 5.5), (51.5, 5), (52.5, 6.0)]
    # test_locations = [(52, 5.5), (48, 0)]

    """Lithuania"""
    train_locations = [(55.0, 23.5), (55.0, 24.0), (55.5, 23.5)]
    test_locations = [(52, 5.5), (55.0, 23.5)]

    crop_features = defaults.get_default_crop_features(pcse_env=args.environment)
    weather_features = defaults.get_default_weather_features()
    action_features = defaults.get_default_action_features()

    tag = f'Seed-{args.seed}'

    kwargs = {'args_vrr': args.vrr, 'limit_action': args.limit_action}
    if not args.measure:
        action_spaces = gymnasium.spaces.Discrete(7)
    else:
        if args.environment:
            po_features = ['TAGP', 'LAI', 'NAVAIL', 'NuptakeTotal', 'SM']
            if 'random' in po_features:
                crop_features.append('random')
        else:
            po_features = ['TGROWTH', 'LAI', 'TNSOIL', 'NUPTT', 'TRAIN']
        kwargs['po_features'] = po_features
        kwargs['args_measure'] = True
        a_shape = [7] + [2] * len(po_features)
        action_spaces = gymnasium.spaces.MultiDiscrete(a_shape)

    train(log_dir, train_years=train_years, test_years=test_years,
          train_locations=train_locations,
          test_locations=test_locations,
          n_steps=args.nsteps, seed=args.seed,
          tag=tag, costs_nitrogen=args.costs_nitrogen,
          crop_features=crop_features,
          weather_features=weather_features,
          action_features=action_features, action_space=action_spaces,
          pcse_model=args.environment, agent=args.agent,
          reward=args.reward, **kwargs)
