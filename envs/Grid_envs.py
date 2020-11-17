import sys
from abc import ABC

sys.path.append("../")
from scipy.integrate import odeint

DER_num = 6

if DER_num == 4:
    from configs.parameters_der4 import *
    # will support in the near future
    from envs.der4_fn import der_fn
elif DER_num == 6:
    from configs.parameters_der6 import *
    from envs.der6_RL_fn import der_fn
elif DER_num == 20:
    from configs.parameters_der20 import *
    from envs.der20_RL_fn import der_fn
else:
    pass


class GridEnv(ABC):
    def __init__(self, config, discrete=True, rendering=False, episode_length=20, random_seed=0, train_mode=True):
        # Setup gym environment
        self.discrete = discrete
        self.rendering = rendering
        self.T = episode_length
        self.seed = random_seed
        self.agent = config.get('agent')
        self.dt = config.getfloat('sampling_time')
        self.coop_gamma = config.getfloat('coop_gamma')
        self.train_mode = train_mode
        test_seeds = config.get('test_seeds').split(',')
        test_seeds = [int(s) for s in test_seeds]
        self.init_test_seeds(test_seeds)
        # minimum and maximum actions can be determined according to conventional methods
        self.min_action = 490
        self.max_action = 540
        self.states = []
        self.n_a = 10
        self.n_s = 9
        self.obs_mean = obs_mean
        self.obs_std = obs_std
        self.DER_num, self.lines_num, self.loads_num = DER_num, lines_num, loads_num
        self.n_agent = self.DER_num
        self.der_fn = der_fn
        self._init_space()
        self.step_list = []
        self.action_smoothing = 0
        try:
            self.reset()
        except Exception as e:
            self.close()
            raise e

    def reset(self, test_ind=0):
        # for initial random disturbance
        if self.train_mode:
            seed = self.seed
        else:
            seed = self.test_seeds[test_ind]
        np.random.seed(seed)
        # random seed for step function
        self.seed += 1
        self.t = 1
        self.pf_fail = False
        # initialize fingerprint
        self.update_fingerprint(self._init_policy())
        self.index = 0
        self.vbuses = []
        self.time = []
        self.step_list.append(1)  # used to store steps when all the voltages are in normal range
        self.action_list = [[] for x in range(self.DER_num)]
        self.x0 = x0
        # 0.4s for stabling the primary control
        t = np.linspace(0, 0.4, 11)
        # random disturbance
        self.disturbance_R = np.random.rand(self.loads_num) * 0.4 + 0.8
        self.disturbance_L = np.random.rand(self.loads_num) * 0.4 + 0.8

        self.x0 = odeint(self.der_fn, self.x0, t, args=(self.disturbance_R, self.disturbance_L), atol=1e-10,
                         rtol=1e-11, mxstep=5000)
        for j in range(self.DER_num):
            # voltage of buses
            self.vbuses.append(((self.x0[:, self.DER_num * 6 + self.lines_num * 2 + self.loads_num * 2 + j + 1] - nq[j] * self.x0[:,
                                                                                                                    5 * j + 3]) / Vnom).tolist())
        self.time.extend(t)
        self.x0 = (self.x0[-1]).tolist()
        return self._get_state()

    def step(self, action, mode='train'):
        if self.discrete:
            # discrete control
            action = (self.max_action - self.min_action) / self.n_a * action + self.min_action
        else:
            # continuous control
            action = 0.5 * (action + 1) * (self.max_action - self.min_action) + self.min_action
        action = np.clip(action, self.min_action, self.max_action).astype(np.float32)

        # used to record actions
        for i in range(self.DER_num):
            self.action_list[i].append(action[i])

        # action smoothing
        if self.t == 1:
            self.action_past = action
        action = self.action_past * self.action_smoothing + action * (
                1.0 - self.action_smoothing)

        self.x0[(6 * self.DER_num + 2 * self.lines_num + 2 * self.loads_num + 1):(
                    7 * self.DER_num + 2 * self.lines_num + 2 * self.loads_num + 1)] = action.tolist()
        if mode == 'train':
            # random disturbance
            self.disturbance_R = (np.random.rand(self.loads_num) * 0.1 - 0.05) + self.disturbance_R
            self.disturbance_L = (np.random.rand(self.loads_num) * 0.1 - 0.05) + self.disturbance_L
        else:
            self.disturbance_R = 0 + self.disturbance_R
            self.disturbance_L = 0 + self.disturbance_L

        self.x0 = odeint(self.der_fn, self.x0,
                         np.array([(self.t - 1) * self.dt, self.t * self.dt]),
                         args=(self.disturbance_R, self.disturbance_L),
                         atol=1e-10,
                         rtol=1e-11, mxstep=5000)

        self.x0 = self.x0[-1]
        obs = self._get_state()
        self.states.append(obs)
        reward = self.get_reward()
        done = self._check_termination()
        global_reward = np.sum(reward)
        self.t += 1
        # use original rewards in test
        if not self.train_mode:
            return obs, reward, done, global_reward
        if (self.agent == 'greedy') or (self.coop_gamma < 0):
            reward = global_reward
        self.action_past = action
        return obs, reward, done, global_reward

    def _check_termination(self, ):
        # if exceed maximum episode steps
        if self.t >= self.T:
            return True
        # if power flow cannot converge, we rescale the actions, thus it does not happen in our case
        # self.pf_fail = True
        return False

    def get_fingerprint(self):
        return self.fingerprint

    def update_fingerprint(self, policy):
        self.fingerprint = policy

    def _init_policy(self, ):
        # [array()], size: 4x5, if n_agent = 4, n_a = 5
        return [np.ones(self.n_a_ls[i]) / self.n_a_ls[i] for i in range(self.n_agent)]

    def get_der_state(self):
        self.der_state = np.zeros(shape=(self.n_agent, self.n_s))
        for i in range(self.n_agent):
            # calculate the state representation for multi-agent reinforcement learning
            # combined of state_der + state_load + state_bus
            self.der_state[i] = (np.array(
                [self.x0[1 + 5 * i], self.x0[2 + 5 * i], self.x0[3 + 5 * i], self.x0[4 + 5 * i],
                 self.x0[5 + 5 * i], x0[7 * DER_num + 2 * lines_num + 2 * loads_num + 1 + 4 * i],
                 x0[7 * DER_num + 2 * lines_num + 2 * loads_num + 2 + 4 * i],
                 x0[7 * DER_num + 2 * lines_num + 2 * loads_num + 3 + 4 * i],
                 x0[7 * DER_num + 2 * lines_num + 2 * loads_num + 4 + 4 * i]]) -
                                 self.obs_mean[i]) / self.obs_std[i]

    def _get_state(self, ):
        state = []
        # update state fn
        self.get_der_state()
        # wave is required in state
        if self.agent == 'greedy':
            state = self.der_state
        elif self.agent == 'centralized':
            # states for centralized control
            state = np.array(self.x0[1:(5 * self.DER_num + 2 * self.lines_num + 2 * self.loads_num + 1)])
        else:
            for n in range(self.n_agent):
                cur_state = self.der_state[n]
                n_n = np.sum(self.neighbor_mask[n])
                # include wave states of neighbors
                if self.agent.startswith('ia2c'):
                    for j in range(int(n_n)):
                        index = np.where(self.neighbor_mask[n] == 1)[0]
                        # computed in env_utils.VSI_VFctrl_func
                        cur_state = np.concatenate((cur_state, self.der_state[index[j]]))
                if self.agent == 'ia2c_fp':
                    # include fingerprints of neighbors
                    for j in range(int(n_n)):
                        index = np.where(self.neighbor_mask[n] == 1)[0]
                        # computed in env_utils.VSI_VFctrl_func
                        cur_state = np.concatenate((cur_state, self.fingerprint[index[j]]))
                state.append(cur_state)
        return state

    def get_neighbor_action(self, action):
        naction = []
        for i in range(self.n_agent):
            naction.append(action[self.neighbor_mask[i] == 1])
        return naction

    def get_reward(self, ):
        reward = []
        count = 0  # used to count number of normal voltage
        x = np.array(self.x0)
        for j in range(self.DER_num):
            # voltage of buses
            vi = (x[self.DER_num * 6 + self.lines_num * 2 + self.loads_num * 2 + j + 1] - nq[j] * x[5 * j + 3]) / Vnom
            self.vbuses[j].append(vi)
            # compute reward according to voltage control performance
            if self.pf_fail:
                reward.append(-100)
            elif 0.95 <= vi <= 1.05:
                count += 1
                reward.append(0.05 - np.abs(1 - vi))
            elif vi <= 0.8 or vi >= 1.25:
                reward.append(-10)
            else:
                reward.append(- np.abs(1 - vi))
        if count == self.n_agent and self.index == 0:
            self.step_list[-1] = self.t
            self.index = 1
        elif (count != self.n_agent) and (self.index == 0) and (self.t == self.T):
            self.step_list[-1] = self.t
        else:
            pass
        self.time.append(self.t * self.dt + 0.4)
        return np.array(reward)

    def _init_space(self):
        self.n_s_ls = []
        # declare for IA2C
        self.neighbor_mask = Physical_Graph
        self.distance_mask = Distance_Mask
        for i in range(self.n_agent):
            # initialize state space
            if not self.agent.startswith('ma2c'):
                n_n = np.sum(self.neighbor_mask[i])
                self.n_s_ls.append(self.n_s * (1 + n_n))
            else:
                self.n_s_ls.append(self.n_s)  # DER state*5 + load_state*2 + Bus_state*2
        self.n_a_ls = [self.n_a] * self.n_agent

    def init_test_seeds(self, test_seeds):
        self.test_num = len(test_seeds)
        self.test_seeds = test_seeds

    def output_data(self, output_path, test_ind):
        np.save(output_path + 'voltage/voltage_' + '{}'.format(test_ind), self.vbuses)
        np.save(output_path + 'voltage/times_' + '{}'.format(test_ind), self.time)
        np.save(output_path + 'voltage/action_list_' + '{}'.format(test_ind), self.action_list)

    def terminate(self):
        return

    def render(self, mode='human'):
        pass
