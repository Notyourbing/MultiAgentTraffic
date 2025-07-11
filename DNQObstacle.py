import random
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from collections import deque
import matplotlib.pyplot as plt
from matplotlib import colors
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from matplotlib.animation import FuncAnimation
import time

GRID_ROWS = 8           # 网格行数
GRID_COLS = 8           # 网格列数
NUM_AGENTS = 4          # 智能体个数
NUM_EPISODES = 400      # 训练轮数

# ----------------------------------------
# 第一部分：定义 TrafficRoutingEnv 环境（加入固定障碍物）
# ----------------------------------------
class TrafficRoutingEnv:
    def __init__(self, grid_size=(5, 5), num_agents=2, obstacles=None):
        """
        新增参数：
            obstacles: List of (row, col) 坐标，表示固定不可通过的障碍物。
                       如果为 None，则默认没有障碍物。
        """
        # 设置随机种子，确保可复现
        random.seed(41)
        np.random.seed(41)
        torch.manual_seed(41)

        self.grid_size = grid_size
        self.num_agents = num_agents

        # ① 只在初始化时随机生成一套起点，并把它存下来
        self._initial_positions = {
            i: (np.random.randint(self.grid_size[0]),
                np.random.randint(self.grid_size[1]))
            for i in range(self.num_agents)
        }
        # ② 目的地在初始化时生成并保持不变
        self.destinations = {
            i: (np.random.randint(self.grid_size[0]),
                np.random.randint(self.grid_size[1]))
            for i in range(self.num_agents)
        }

        # ③ 第一次“真正地”把 agent_positions 设为初始的那份
        self.agent_positions = dict(self._initial_positions)

        # ④ 初始化 arrived 标记和步数
        self._arrived = {i: False for i in range(self.num_agents)}
        self.steps = 0

        # ⑤ 初始化障碍物列表
        if obstacles is None:
            self.obstacles = []  # 没有障碍物
        else:
            self.obstacles = obstacles.copy()

        # 确保障碍物不会与初始位置或目的地冲突
        for obs in self.obstacles:
            for i in range(self.num_agents):
                if obs == self._initial_positions[i]:
                    raise ValueError(f"障碍物 {obs} 与 Agent {i} 的初始位置冲突！")
                if obs == self.destinations[i]:
                    raise ValueError(f"障碍物 {obs} 与 Agent {i} 的目的地冲突！")

    def reset(self):
        """
        不再随机生成位置，而是把 agent_positions 还原为构造时那套 _initial_positions，
        并把 arrived、steps 清零。目的地 self.destinations 和 self.obstacles 保持不变。
        """
        # 1) 把 agent_locations 复位到构造里存的那份
        self.agent_positions = dict(self._initial_positions)

        # 2) 清空 arrived 标志，步数归零
        self._arrived = {i: False for i in range(self.num_agents)}
        self.steps = 0

        # 3) 返回当前（复位后）的观测
        return self._get_observations()

    def _get_observations(self):
        # 返回每个智能体的当前位置、目标位置，以及障碍物列表（如需将障碍物信息纳入 observation）
        obs = {}
        for i in range(self.num_agents):
            obs[i] = {
                'position': self.agent_positions[i],
                'destination': self.destinations[i],
                # 如果后续算法需要知道分布式障碍，可在 observation 中加入：
                'obstacles': self.obstacles
            }
        return obs

    def step(self, actions):
        # 1. 记录旧位置
        old_positions = {i: self.agent_positions[i] for i in range(self.num_agents)}

        # 2. 计算“期望”新位置（含已到达冻结逻辑 + 障碍物阻塞逻辑）
        new_positions = {}
        for i, action in actions.items():
            if self._arrived.get(i, False):
                new_positions[i] = old_positions[i]
                continue

            x, y = old_positions[i]
            if action == 0 and x > 0:                  # 上
                x -= 1
            elif action == 1 and x < self.grid_size[0] - 1:  # 下
                x += 1
            elif action == 2 and y > 0:                # 左
                y -= 1
            elif action == 3 and y < self.grid_size[1] - 1:  # 右
                y += 1
            # action == 4 (停留) 或越界时，x,y 保持不变

            # 如果“期望”新位置是障碍物，则保持在原地
            if (x, y) in self.obstacles:
                new_positions[i] = old_positions[i]
            else:
                new_positions[i] = (x, y)

        # 3. 检测并标记“交换碰撞”
        swap_blocked = set()
        for i in range(self.num_agents):
            for j in range(i + 1, self.num_agents):
                if (new_positions[i] == old_positions[j]
                        and new_positions[j] == old_positions[i]):
                    swap_blocked.add(i)
                    swap_blocked.add(j)

        # 4. 检测并标记“同格碰撞”
        from collections import defaultdict
        desired_cells = defaultdict(list)
        for i in range(self.num_agents):
            if i in swap_blocked:
                continue
            desired_cells[new_positions[i]].append(i)

        collision_blocked = set()
        for cell, agents in desired_cells.items():
            if len(agents) > 1:
                for a in agents:
                    collision_blocked.add(a)

        # 统计碰撞次数
        num_collisions = len(swap_blocked) + len(collision_blocked)

        # 5. 合并所有需要阻塞的智能体
        blocked = swap_blocked.union(collision_blocked)

        # 6. 决定每个智能体的最终位置
        final_positions = {}
        for i in range(self.num_agents):
            if i in blocked:
                final_positions[i] = old_positions[i]
            else:
                final_positions[i] = new_positions[i]

        # 更新位置
        self.agent_positions = final_positions
        self.steps += 1

        # 7. 计算原始奖励和 dones（到达目标 +10，否则 -1）
        raw_rewards = {}
        dones = {}
        for i in range(self.num_agents):
            if self.agent_positions[i] == self.destinations[i]:
                if not self._arrived[i]:
                    raw_rewards[i] = +10
                    self._arrived[i] = True
                else:
                    raw_rewards[i] = 0
                dones[i] = True
            else:
                raw_rewards[i] = -1
                dones[i] = False

        # 8. 利用“旧距离 / 新距离”做潜力塑形，计算最终奖励
        shaped_rewards = {}
        gamma = 0.99
        for i in range(self.num_agents):
            ox, oy = old_positions[i]
            gx, gy = self.destinations[i]
            dist_old = abs(ox - gx) + abs(oy - gy)

            nx, ny = self.agent_positions[i]
            dist_new = abs(nx - gx) + abs(ny - gy)

            phi_old = -dist_old
            phi_new = -dist_new

            # 下面这行强制转成 float32
            reward32 = np.float32(raw_rewards[i] + (gamma * phi_new - phi_old))
            shaped_rewards[i] = reward32

        # 9. 回合结束标志
        done = all(dones.values()) or (self.steps >= 50)
        return self._get_observations(), shaped_rewards, done, {}, num_collisions

    def render(self):
        # 可视化网格、障碍物、智能体和目标
        H, W = self.grid_size
        grid = np.zeros((H, W), dtype=int)

        # 0 表示空白
        # 1 表示目标（蓝色）
        # 2 表示智能体（红色）
        # 3 表示障碍物（黑色）
        for (ox, oy) in self.obstacles:
            grid[ox, oy] = 3

        for i in range(self.num_agents):
            x, y = self.destinations[i]
            # 如果目的地恰好在障碍物上，根据需求可抛出错误或忽略。此处假设不冲突：
            if grid[x, y] == 3:
                raise ValueError(f"目的地 {(x, y)} 与障碍物冲突！")
            grid[x, y] = 1  # 目标标蓝色

        for i in range(self.num_agents):
            x, y = self.agent_positions[i]
            # 如果智能体恰好踩在障碍物上，说明逻辑有问题，此处抛出错误以便调试
            if grid[x, y] == 3:
                raise ValueError(f"Agent {i} 企图进入障碍物 {(x, y)}！")
            # 如果目的地和智能体重叠，根据优先级，智能体覆盖目标
            grid[x, y] = 2  # 智能体标红色

        # 定义带四种颜色的 colormap
        cmap = colors.ListedColormap(['white', 'blue', 'red', 'black'])
        bounds = [0, 1, 2, 3, 4]
        norm = colors.BoundaryNorm(bounds, cmap.N)

        plt.figure(figsize=(5, 5))
        plt.imshow(grid, cmap=cmap, norm=norm)
        plt.title(f"Step: {self.steps}")
        plt.show()


# ----------------------------------------
# 第二部分：定义 DQN 及多智能体训练器
# ----------------------------------------
# 1. DQN 网络结构
class DQN(nn.Module):
    def __init__(self, state_dim, action_dim, hidden_dim=64):
        super(DQN, self).__init__()
        self.model = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, action_dim)
        )

    def forward(self, x):
        return self.model(x)

# 2. 经验回放缓冲区
class ReplayBuffer:
    def __init__(self, capacity):
        self.capacity = capacity
        self.buffer = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done):
        self.buffer.append((state, action, reward, next_state, done))

    def sample(self, batch_size):
        batch = random.sample(self.buffer, batch_size)
        state, action, reward, next_state, done = map(np.array, zip(*batch))
        return state, action, reward, next_state, done

    def __len__(self):
        return len(self.buffer)

# 3. 辅助函数：将观测转换为网络输入的 state 向量
def obs_to_state(obs, grid_size):
    """
    扩展状态：在 14 维（4坐标+9邻居+1归一化曼哈顿距离）的基础上，
    同时将障碍物也视作占据单元。
    最终 state 维度：14。
    """
    state_dict = {}
    H, W = grid_size
    # occupancy 标记网格中哪些位置被“占据”——智能体或障碍物
    occupancy = np.zeros((H, W), dtype=np.float32)
    # 先把障碍物设为占据
    if 'obstacles' in next(iter(obs.values())):
        for (ox, oy) in next(iter(obs.values()))['obstacles']:
            occupancy[ox, oy] = 1.0

    # 再把所有智能体设为占据
    for j in range(len(obs)):
        rx, ry = obs[j]['position']
        occupancy[rx, ry] = 1.0

    for agent_id, info in obs.items():
        x, y = info['position']
        dx, dy = info['destination']

        # 4 维归一化坐标
        pos_vec = np.array([x/(H-1), y/(W-1), dx/(H-1), dy/(W-1)], dtype=np.float32)

        # 3x3 邻居信息
        local = np.zeros((3, 3), dtype=np.float32)
        for dx_off in (-1, 0, 1):
            for dy_off in (-1, 0, 1):
                nx, ny = x + dx_off, y + dy_off
                if 0 <= nx < H and 0 <= ny < W:
                    local[dx_off+1, dy_off+1] = occupancy[nx, ny]
        local_flat = local.flatten()  # 9 维

        # 归一化曼哈顿距离
        dist = abs(x - dx) + abs(y - dy)
        # 最大可能距离约为 (H-1)+(W-1)，所以归一化为 [0,1]
        dist_norm = np.array([dist / ((H - 1) + (W - 1))], dtype=np.float32)

        # 拼接：4 + 9 + 1 = 14 维
        state = np.concatenate([pos_vec, local_flat, dist_norm], axis=0)
        state_dict[agent_id] = state

    return state_dict


# 4. 多智能体 DQN 训练器
class MADQNTrainer:
    def __init__(self, env, num_agents, state_dim, action_dim,
                 buffer_capacity=5000, batch_size=64,
                 gamma=0.99, lr=1e-3, target_update=10):
        self.env = env
        self.num_agents = num_agents
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.batch_size = batch_size
        self.gamma = gamma
        self.target_update = target_update

        # 为每个智能体分别创建策略网络、目标网络、优化器和回放缓冲区
        self.policy_nets = {}
        self.target_nets = {}
        self.optimizers = {}
        self.replay_buffers = {}

        for i in range(num_agents):
            policy_net = DQN(state_dim, action_dim)
            target_net = DQN(state_dim, action_dim)
            target_net.load_state_dict(policy_net.state_dict())
            optimizer = optim.Adam(policy_net.parameters(), lr=lr)
            buffer = ReplayBuffer(buffer_capacity)

            self.policy_nets[i] = policy_net
            self.target_nets[i] = target_net
            self.optimizers[i] = optimizer
            self.replay_buffers[i] = buffer

        self.steps_done = 0

        # 新增：用于记录loss和碰撞次数的容器
        self.loss_history = {i: [] for i in range(num_agents)}  # 每个agent的loss历史
        self.collision_history = []  # 每回合的碰撞次数
        self.episode_collisions = 0  # 当前回合的碰撞计数

    def select_action(self, agent_id, state, eps, done):
        """
        ε-贪心策略选择动作
        state: numpy 数组
        """
        if done:
            return 4  # 已经到达目标后持续停留

        if random.random() < eps:
            return random.randint(0, self.action_dim - 1)
        else:
            state_tensor = torch.from_numpy(state).unsqueeze(0)  # shape [1, state_dim]
            with torch.no_grad():
                q_values = self.policy_nets[agent_id](state_tensor)
            return int(q_values.argmax().item())

    def optimize_agent(self, agent_id):
        """
        对单个智能体进行 DQN 更新
        """
        buffer = self.replay_buffers[agent_id]
        if len(buffer) < self.batch_size:
            return None  # 返回None表示没有更新

        states, actions, rewards, next_states, dones = buffer.sample(self.batch_size)

        # 转换为张量
        states = torch.from_numpy(states)              # [batch, state_dim]
        actions = torch.from_numpy(actions).unsqueeze(1).long()  # 转成 long (int64)
        rewards = torch.from_numpy(rewards).unsqueeze(1)   # [batch, 1]
        next_states = torch.from_numpy(next_states)    # [batch, state_dim]
        dones = torch.from_numpy(dones.astype(np.uint8)).unsqueeze(1)  # [batch, 1]

        # 计算当前 Q(s,a)
        current_q = self.policy_nets[agent_id](states).gather(1, actions)

        # 计算目标 Q 值：r + γ * max_a' Q_target(next_s, a')
        with torch.no_grad():
            next_q = self.target_nets[agent_id](next_states).max(1)[0].unsqueeze(1)
            target_q = rewards + (self.gamma * next_q * (1 - dones))

        # MSE 误差
        criterion = nn.MSELoss()
        loss = criterion(current_q, target_q)

        # 反向传播并优化
        self.optimizers[agent_id].zero_grad()
        loss.backward()
        self.optimizers[agent_id].step()

        # 在反向传播后记录loss
        loss_value = loss.item()
        self.loss_history[agent_id].append(loss_value)
    
        return loss_value  # 返回loss值

    def update_targets(self):
        """
        将 policy 网络的权重复制到 target 网络
        """
        for i in range(self.num_agents):
            self.target_nets[i].load_state_dict(self.policy_nets[i].state_dict())

    def _plot_training_stats(self, returns, losses, collisions):
        """绘制训练统计图：回报、loss和碰撞次数"""
        plt.figure(figsize=(15, 5))
    
        # 计算移动平均（窗口大小为10）
        window_size = 10
        returns_smooth = np.convolve(returns, np.ones(window_size)/window_size, mode='valid')
        losses_smooth = np.convolve([x for x in losses if x > 0], np.ones(window_size)/window_size, mode='valid')
        collisions_smooth = np.convolve(collisions, np.ones(window_size)/window_size, mode='valid')
    
        # 1. 回报曲线（原始数据+平滑曲线）
        plt.subplot(1, 3, 1)
        plt.plot(returns, alpha=0.3, label="Raw Return")
        plt.plot(range(window_size-1, len(returns)), returns_smooth, label=f"MA({window_size}) Return", color='blue')
        plt.xlabel("Episode")
        plt.ylabel("Total Reward")
        plt.title("Training Returns")
        plt.legend()
        plt.grid(True)
    
        # 2. Loss曲线（只绘制有效loss点）
        valid_losses = [x for x in losses if x > 0]
        plt.subplot(1, 3, 2)
        plt.plot(valid_losses, alpha=0.3, label="Raw Loss", color='orange')
        plt.plot(range(window_size-1, len(valid_losses)), losses_smooth, 
                 label=f"MA({window_size}) Loss", color='orange')
        plt.xlabel("Episode")
        plt.ylabel("Average Loss")
        plt.title("Training Loss")
        plt.legend()
        plt.grid(True)
    
        # 3. 碰撞次数曲线（原始数据+平滑曲线）
        plt.subplot(1, 3, 3)
        plt.plot(collisions, alpha=0.3, label="Raw Collisions", color='red')
        plt.plot(range(window_size-1, len(collisions)), collisions_smooth, 
                 label=f"MA({window_size}) Collisions", color='red')
        plt.xlabel("Episode")
        plt.ylabel("Collisions per Episode")
        plt.title("Collision Count")
        plt.legend()
        plt.grid(True)
    
        plt.tight_layout()
        plt.savefig('training_results/DQNObstacle.png', dpi=300, bbox_inches='tight')
        plt.show()


    def train(self, num_episodes=200, max_steps=50,
              eps_start=1.0, eps_end=0.05, eps_decay=0.995):
        """
        多智能体 DQN 训练函数（修正版）。主要改动点保持不变。
        """
        eps = eps_start
        episode_returns = []
        avg_loss_history = []  # 新增：每回合平均loss

        # 新增：收敛评估变量
        start_time = time.time()  # 记录训练开始时间
        convergence_episode = None  # 收敛轮次
        convergence_threshold = 0.9  # 定义收敛阈值（可根据实际情况调整）
        max_return = 0  # 记录最大回报

        for episode in range(1, num_episodes + 1):
            # 重置环境，并初始化 done_dict
            obs = self.env.reset()
            state_dict = obs_to_state(obs, self.env.grid_size)
            done_dict = {i: False for i in range(self.num_agents)}
            total_reward = 0
            self.episode_collisions = 0  # 重置碰撞计数器

            for step in range(max_steps):
                actions = {}
                # ε-贪心选动作，若 done_dict[i]==True，则 select_action 会返回 4（stay）
                for i in range(self.num_agents):
                    actions[i] = self.select_action(i, state_dict[i], eps, done_dict[i])

                # 与环境交互
                next_obs, rewards, done_all, _, num_collisions = self.env.step(actions)
                self.episode_collisions += num_collisions
                next_state_dict = obs_to_state(next_obs, self.env.grid_size)

                # 存储 transition、更新 done_dict
                for i in range(self.num_agents):
                    # 存储的是“执行动作之前”的 done 状态
                    self.replay_buffers[i].push(
                        state_dict[i],
                        actions[i],
                        rewards[i],
                        next_state_dict[i],
                        done_dict[i]
                    )

                    # 如果这一步 reward == +10，说明 i 刚刚到达目标，标记 done=True
                    if rewards[i] == 10:
                        done_dict[i] = True

                    total_reward += rewards[i]

                state_dict = next_state_dict

                # 对每个 Agent 做一次 DQN 更新
                for i in range(self.num_agents):
                    self.optimize_agent(i)

                # 如果所有 Agent 都到达，或者步数上限，结束本回合
                if done_all:
                    break

             # 在回合结束后记录数据
            episode_returns.append(total_reward)
            self.collision_history.append(self.episode_collisions)

            # 更新最大回报
            if total_reward > max_return:
                max_return = total_reward
        
            # 检查是否收敛（连续10回合平均回报达到最大可能回报的90%）
            if len(episode_returns) >= 10 and convergence_episode is None:
                recent_avg = np.mean(episode_returns[-10:])
                if recent_avg >= max_return * convergence_threshold:
                    convergence_episode = episode
                    convergence_time = time.time() - start_time
        
            # 计算本回合的平均loss
            episode_losses = []
            for i in range(self.num_agents):
                if self.loss_history[i]:  # 只取本回合的loss
                    episode_losses.append(np.mean(self.loss_history[i][-max_steps:]))
            avg_loss = np.mean(episode_losses) if episode_losses else 0.0
            avg_loss_history.append(avg_loss)

            # ε 衰减
            eps = max(eps * eps_decay, eps_end)

            # 周期性地同步 target 网络
            if episode % self.target_update == 0:
                self.update_targets()

            # 每10回合打印一次平均回报、loss和碰撞次数
            if episode % 10 == 0:
                last10_avg = np.mean(episode_returns[-10:])
                last10_loss = np.mean(avg_loss_history[-10:])
                last10_coll = np.mean(self.collision_history[-10:])
                print(f"Episode {episode}/{num_episodes}, "
                      f"Epsilon: {eps:.3f}, "
                      f"AvgReturn(last10): {last10_avg:.2f}, "
                      f"AvgLoss(last10): {last10_loss:.4f}, "
                      f"AvgCollisions(last10): {last10_coll:.1f}")

        # 训练完成后输出收敛评估结果,绘制统计图
        if convergence_episode is not None:
            print(f"\n算法在 {convergence_episode} 轮后收敛，花费时间: {convergence_time:.2f} 秒")
            print(f"最终10轮平均回报: {np.mean(episode_returns[-10:]):.2f}")
        else:
            print("\n算法在指定轮数内未达到收敛标准")
            print(f"最终10轮平均回报: {np.mean(episode_returns[-10:]):.2f}")
        self._plot_training_stats(episode_returns, avg_loss_history, self.collision_history)
    
        return episode_returns





def animate(trainer, env):
    """
    可视化函数：动画展示贪心策略下的多智能体轨迹，
    从第一步开始逐步显示每个智能体的移动过程，
    障碍物绘制为填满整个格子，并在图例中说明起点、终点和障碍物的形状。
    """
    H, W = env.grid_size
    agent_trajectories = {i: [] for i in range(env.num_agents)}
    done_dict = {i: False for i in range(env.num_agents)}

    # 重置环境并记录初始位置
    obs = env.reset()
    state_dict = obs_to_state(obs, env.grid_size)
    for i in range(env.num_agents):
        agent_trajectories[i].append(env.agent_positions[i])

    # 收集所有步骤的数据用于动画
    all_steps = []
    step = 0
    done = False
    
    while not done and step < 50:
        actions = {
            i: trainer.select_action(i, state_dict[i], eps=0.0, done=done_dict[i])
            for i in range(env.num_agents)
        }
        next_obs, rewards, done, _, num_collisions = env.step(actions)
        next_state_dict = obs_to_state(next_obs, env.grid_size)
        
        for i in range(env.num_agents):
            if rewards[i] == 10 or env.agent_positions[i] == env.destinations[i]:
                done_dict[i] = True
            agent_trajectories[i].append(env.agent_positions[i])
        
        # 记录当前步骤的所有智能体位置
        all_steps.append({i: env.agent_positions[i] for i in range(env.num_agents)})
        state_dict = next_state_dict
        step += 1

    print("Final Greedy Trajectories (row, col) for each agent:")
    for i, traj in agent_trajectories.items():
        print(f"Agent {i}: {traj}")

    # 创建图形
    fig, ax = plt.subplots(figsize=(8, 8))
    
    # 1. 绘制网格线
    for x in range(H + 1):
        ax.plot([0, W], [x, x], color='gray', linewidth=0.5)
    for y in range(W + 1):
        ax.plot([y, y], [0, H], color='gray', linewidth=0.5)

    # 2. 绘制障碍物（填满整个格子）
    for (ox, oy) in env.obstacles:
        rect = Rectangle((oy, H - 1 - ox), 1, 1, facecolor='black', edgecolor='black')
        ax.add_patch(rect)

    # 3. 绘制每个智能体的起点和终点
    base_colors = ['r', 'g', 'b', 'c', 'm', 'y', 'k', '#FFA500']
    colors_list = [base_colors[i % len(base_colors)] for i in range(env.num_agents)]
    
    # 绘制目的地（终点）
    destinations = {}
    for i in range(env.num_agents):
        goal = env.destinations[i]
        gx, gy = goal[1] + 0.5, (H - 1 - goal[0]) + 0.5
        destinations[i] = ax.scatter([gx], [gy],
                                   color=colors_list[i],
                                   marker='*',
                                   s=120,
                                   zorder=3)

    # 初始化智能体位置标记
    agents = {}
    for i in range(env.num_agents):
        start = agent_trajectories[i][0]
        sx, sy = start[1] + 0.5, (H - 1 - start[0]) + 0.5
        agents[i] = ax.scatter([sx], [sy],
                             color=colors_list[i],
                             marker='s',
                             s=80,
                             zorder=3)

    # 初始化轨迹线
    lines = {}
    for i in range(env.num_agents):
        lines[i], = ax.plot([], [], 
                           color=colors_list[i],
                           marker='o',
                           markersize=6,
                           linewidth=2,
                           zorder=2)

    # 设置坐标轴
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("Multi-Agent Trajectories Animation (Greedy Policy)")

    # 添加步骤计数器文本
    step_text = ax.text(W/2, H+0.5, "Step: 0", ha='center', va='center', fontsize=12)

    # 自定义图例
    legend_elements = [
        Rectangle((0, 0), 1, 1, facecolor='black', edgecolor='black', label='Obstacle'),
        Line2D([0], [0], marker='s', color='gray', label='Start',
               markerfacecolor='none', linestyle='None', markersize=10),
        Line2D([0], [0], marker='*', color='gray', label='Goal',
               markerfacecolor='none', linestyle='None', markersize=12)
    ]
    for i, color in enumerate(colors_list):
        legend_elements.append(Line2D([0], [0], color=color, lw=2, label=f'Agent {i}'))
    
    ax.legend(handles=legend_elements,
             bbox_to_anchor=(1.01, 1),
             loc='upper left',
             fontsize=9)

    # 动画更新函数
    def update(frame):
        nonlocal step_text
        
        # 更新步骤文本
        step_text.set_text(f"Step: {frame+1}")
        
        # 更新每个智能体的位置和轨迹
        for i in range(env.num_agents):
            # 只更新到当前帧的位置
            current_traj = agent_trajectories[i][:frame+2]  # +2因为初始位置在0帧
            
            # 更新智能体位置
            if frame < len(all_steps):
                x, y = all_steps[frame][i]
                agents[i].set_offsets([y + 0.5, (H - 1 - x) + 0.5])
            
            # 更新轨迹线
            xs = [pos[1] + 0.5 for pos in current_traj]
            ys = [(H - 1 - pos[0]) + 0.5 for pos in current_traj]
            lines[i].set_data(xs, ys)
        
        return list(agents.values()) + list(lines.values()) + [step_text]

    # 创建动画
    ani = FuncAnimation(
        fig, 
        update, 
        frames=len(all_steps),  # 使用实际步骤数作为帧数
        interval=500,  # 每500毫秒更新一帧
        blit=True,
        repeat=False
    )

    plt.tight_layout()
    plt.show()
    
    # 保存为GIF
    ani.save('animate_results/greedy_trajectories_DQNObstacle.gif', writer='pillow', fps=2, dpi=100)
    return ani


# ----------------------------------------
# 第三部分：执行训练（示例：定义一些固定障碍物）
# ----------------------------------------
if __name__ == "__main__":
    # 在 8x8 网格中，定义几个固定障碍物坐标
    fixed_obstacles = [
        (1, 1),
        (3, 0),
        (2, 2),
        (2, 3),
        (2, 4),
        (3, 2),
        (5, 5),
        (6, 2),
    ]

    # 1. 用前面定义的变量来创建环境和 Trainer
    env = TrafficRoutingEnv(
        grid_size=(GRID_ROWS, GRID_COLS),
        num_agents=NUM_AGENTS,
        obstacles=fixed_obstacles
    )

    state_dim = 14   # 因为我们用 4+9+1 的状态向量
    action_dim = 5   # 上、下、左、右、停留

    trainer = MADQNTrainer(
        env=env,
        num_agents=NUM_AGENTS,
        state_dim=state_dim,
        action_dim=action_dim
    )

    # 进行训练
    #episode_returns = trainer.train(num_episodes=NUM_EPISODES)
    trainer.train(num_episodes=NUM_EPISODES)

    # 训练结束后可视化（贪心策略）
    #plot_greedy_trajectories(trainer, env)
    animate(trainer, env)
