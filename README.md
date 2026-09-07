# A股底部首板看板

个人使用的 A 股选股辅助工具，维护**两个互相独立的股票池**：

- **股票池一：底部首板**——每天扫描一次，找出涨停首板信号，追踪它们涨停后约5个
  交易日回踩到位的情况（`daily_scan.py`）。
- **股票池二：地量后放量突破**——不要求涨停，找市值不大、此前地量、当日突然放量
  但价格还没涨很多的股票（`scan_volume_surge.py`，跟股票池一完全独立）。

纯只读展示，没有下单接口，也没有任何"刷新/更新"按钮——所有数据更新都在这两个
扫描脚本里，网页只负责展示结果。

## 选股逻辑

### 股票池一：底部首板

"底部首板"的判断只有两条：

1. 首板：此前30个交易日内没有涨停过（`FIRST_LIMIT_UP_LOOKBACK_DAYS`），排除
   连板/接力股，只要真正从底部启动的首板。
2. 底部：涨停当日收盘价，没有超过此前30个交易日平均收盘价的1.4倍
   （`MA_PRICE_WINDOW` + `MA_PRICE_MAX_RATIO`，即最多允许比均价高30%~50%）——
   不看地量、不看均线是否走平钝化，就这一条价格判断。

然后：

3. 重点关注（这一步也已用多进程并发刷新，观察池股票数越多提速越明显）：第一个
   涨停板后**观察期20个交易日**（`PULLBACK_WINDOW_DAYS`）内，
   **回踩到涨停价位本身**的约5%左右（默认3%~7%区间）——比如涨停是90到100，回踩
   指的是回到93~97这个区间，不是如果股票后续继续上涨创出新高、从那个更高的价位
   往下算的回调。20个交易日内没回踩到位就标记"观察期结束"，移出重点关注。对这些
   股票额外标注 **MACD 确认**：近30个交易日内是否形成过金叉（DIF上穿DEA），且金叉之后
   DIF-DEA 持续向上发散（多头动能增强，不是叉完就死叉打回）。这只是多给的一个
   参考标注，不影响是否进入"回调买点区间"这个列表本身。
4. 结合股票所属板块的当日热门度（涨停股池接口自带"所属行业"，另外还会尝试标注
   更精确的板块涨跌幅）。

范围：沪深主板 + 创业板 + 科创板，不含北交所、不含 ST。

### 股票池二：地量后放量突破

不要求涨停，条件（同时满足，"当日"以运行 `scan_volume_surge.py` 那一刻能拿到的
最新交易日为准）：

1. 总市值 < 200亿人民币（`MARKET_CAP_MAX_YI`）；
2. 此前30个交易日平均换手率 < 2%（`VOLUME_SURGE_LOOKBACK_DAYS` +
   `VOLUME_SURGE_AVG_TURNOVER_MAX_PCT`），说明此前确实是地量；
3. 当日换手率 >= 前30日平均换手率的2倍以上（`VOLUME_SURGE_RATIO`），说明突然放量；
4. 当日收盘价 < 前30日平均价格的1.2倍（`VOLUME_SURGE_PRICE_MAX_RATIO`），说明价格
   还没涨出去太多。

市值过滤依赖东方财富实时快照的总市值字段，这个字段没有新浪备用数据源。东方财富能
连上时，除了当次使用还会顺带把这份市值快照缓存进本地数据库；东方财富连不上时自动
退回上一次成功缓存的市值快照（哪怕缓存有几天旧了，也比完全不限市值扫全市场强）；
只有"从来没成功缓存过"才会真的不限市值。

这个股票池即使市值过滤生效了，剩下的仍有大几千只要逐个拉历史算30日平均，比股票池
一的"涨停股池快速方案"慢很多，默认已经用多进程并发（8个进程，实测约5倍提速），
仍然建议找空闲时间（比如周末）单独跑一次，不用跟 `daily_scan.py` 绑在一起每天跑：

```bash
python scan_volume_surge.py --limit 300   # 先测试
python scan_volume_surge.py                # 完整扫描
python scan_volume_surge.py --workers 16   # 调整并发进程数（默认8）
```

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 每天怎么用

```bash
source .venv/bin/activate

# 1. 扫描（收盘后跑，比如每天下午跑一次）
python daily_scan.py

# 2. 打开看板看结果
streamlit run app.py
```

`daily_scan.py` 有两种方案：

- **快速方案（默认）**：覆盖沪深主板+创业板+科创板，分两步拼出这个范围：
  1. 用东方财富"涨停股池"接口直接拿到近7个交易日涨停过的股票——但这个接口本身
     不含科创板（数据源自己的限制），所以这一步只覆盖沪深主板+创业板，是一个很
     小的名单（通常几百只以内）。
  2. 额外对科创板（688/689，不到1000只）单独做一次全量历史扫描，补上涨停股池
     覆盖不到的部分。
  两步的候选股票再做完整的历史校验，默认用多进程并发（8个进程，实测约5倍提速）。
  比全市场扫描快很多，但因为科创板要单独全量扫一遍，没有"只查候选名单"那么快。
  ```bash
  python daily_scan.py                        # 近7个交易日，含科创板（默认）
  python daily_scan.py --days 10               # 改成近10个交易日
  python daily_scan.py --skip-star-market       # 跳过科创板，只查涨停股池名单（更快，但不覆盖科创板）
  python daily_scan.py --workers 16             # 调整并发进程数（默认8）
  ```

- **全市场方案**：对沪深主板+创业板+科创板全市场逐只扫描（不依赖涨停股池接口，
  能覆盖到更早期、已经不在"近N日涨停"名单里但仍符合条件的历史信号）。第一次跑
  会比较慢（受限于数据源限速），建议先用 `--limit` 小范围测试。
  ```bash
  python daily_scan.py --full-universe --limit 300   # 先测试
  python daily_scan.py --full-universe                # 完整全市场扫描（收盘后跑，可能要跑很久）
  ```

## 每天怎么发布成网站（给朋友看）

`app.py`（Streamlit）是你自己本地用的交互看板；`index.html` 是给朋友看的静态网页——
`generate_site.py` 会把数据库里最新的内容打包成一个自包含的 HTML 文件（不需要服务器，
不依赖任何 CDN），推到 GitHub、开启 Pages 之后就是一个真正的网址。

**一次性设置**：

1. 在 [github.com](https://github.com) 新建一个仓库（建议 public，这样 GitHub Pages
   免费直接可用；如果要 private，Pages 需要付费版 GitHub 才能用，朋友那边则要么给
   仓库协作者权限，要么走 Streamlit Community Cloud 那条路，更麻烦）。
2. 把本地仓库和远程仓库关联、推第一次：
   ```bash
   git remote add origin <你刚建的仓库地址，比如 https://github.com/你的用户名/仓库名.git>
   git branch -M main
   git push -u origin main
   ```
3. 仓库页面 → Settings → Pages → Source 选 "Deploy from a branch"，Branch 选
   `main` / `(root)`，Save。等一两分钟，页面会给你一个
   `https://你的用户名.github.io/仓库名/` 的网址，这就是发给朋友的链接。

**以后每天更新**（这四步是核心，其余都是一次性的）：

```bash
source .venv/bin/activate
python daily_scan.py          # 1. 扫描股票池一：底部首板
python scan_volume_surge.py   # 2. 扫描股票池二：地量后放量突破
python generate_site.py       # 3. 根据数据库最新内容重新生成 index.html
git add -A && git commit -m "更新每日数据 $(date +%Y-%m-%d)" && git push   # 4. 推到 GitHub
```

推上去之后 GitHub Pages 会自动重新部署（一般一两分钟内生效），不需要在 Settings 里
再点任何东西。这四步已经存成了 `publish.sh`：

```bash
chmod +x publish.sh   # 第一次用要给它执行权限
./publish.sh
```

**已经设置成每天自动跑**，不用自己手动敲命令：用的是 macOS 自带的 launchd（比
cron 更适合 Mac，睡眠唤醒后也能补跑），配置文件在
`~/Library/LaunchAgents/com.jiaying.astock.publish.plist`，工作日下午 15:35 自动
跑一次 `publish.sh`。日志在 `data/publish.log`，出问题可以先看这个文件排查。

```bash
# 加载/激活（改了 plist 之后也要重新执行这条让它生效）
launchctl load -w ~/Library/LaunchAgents/com.jiaying.astock.publish.plist

# 停用（比如出门一段时间不想让它自动跑）
launchctl unload ~/Library/LaunchAgents/com.jiaying.astock.publish.plist

# 手动触发一次（不用等到15:35，立刻跑一次，方便测试）
launchctl start com.jiaying.astock.publish
```

前提是那个时间点 Mac 得是开着、没有关机/完全睡眠的（合盖但接了电源、开着"电源
适配器"下的"防止自动睡眠"一般没问题；如果那个时刻正好在睡眠，launchd 通常会在
下次唤醒后尽快补跑，但不保证）。

数据库文件（`data/astock_toolkit.sqlite3`，含每只股票的历史K线缓存）不会被提交到
GitHub（见 `.gitignore`）——它体积会越滚越大，也没必要公开；真正对外展示的只有
`generate_site.py` 生成的那个精简版 `index.html`。

## 数据源说明（重要）

行情数据来自 [AKShare](https://akshare.akfamily.xyz/)，免费、无需注册。工具内部对每个
数据接口都做了"东方财富优先，失败自动降级到新浪/同花顺"的处理：

- 东方财富接口字段最全（含换手率、涨停股池、板块成分股），但**在部分网络环境下
  （尤其是云服务器、境外 IP）会被限制访问而连接失败**。国内正常家庭/公司网络一般
  没问题。"涨停股池"这个接口用的是东方财富的另一个子域名，实测比其它东方财富接口
  更容易访问成功。
- 若东方财富完全不可用，全市场快照/历史K线/换手率会自动降级为新浪数据源；行业板块
  强度排名降级为同花顺接口。"涨停股池"和"板块成分股"这两个接口目前没有备用数据源，
  不可用时该功能会跳过（不影响其它已经跑通的部分）。
- 新浪的全市场快照接口需要分页拉取约5500只股票，降级模式下第一次请求可能要
  30~60秒；这也是为什么日常应该用快速方案而不是全市场方案。
- 市值数据（东方财富快照的"总市值"字段）没有新浪备用数据源，但会在能连上的时候
  自动缓存到本地（settings表），连不上时用缓存兜底，见上面"股票池二"说明。

**踩过的坑（不要改回多线程）**：几个扫描函数用的是 `concurrent.futures.
ProcessPoolExecutor`（多进程），不是多线程。这是因为新浪历史行情的降级路径
（`stock_zh_a_daily`）内部每次调用都会新建一个 `py_mini_racer.MiniRacer()`
（跑一段JS解密数据），底层是V8引擎——多个线程同时初始化V8会直接让整个Python
进程崩溃（`[FATAL:address_pool_manager.cc(67)]`，不是能 try/except 捕获的
Python异常），这个是实测触发过的，不是猜测。改用多进程后每个子进程有独立的
V8/内存空间，问题就没有了，实测约5倍提速且稳定跑了两次没有崩溃。

改用多进程之后还踩到第二个坑：子进程只要走过一次新浪降级路径，干完活正常退出时会
**卡死不退出**（`sample <pid>` 抓栈能看到卡在 mini_racer 内部一个不会自己停的 V8
消息泵后台线程上，Python 解释器收尾阶段要 join 它，永远 join 不完）。8个子进程里
只要卡住1个，整个脚本的进度条哪怕跑满100%也会假死在原地不动，实测真实触发过（不
是理论推测）。解决办法是给每个子进程注册 `atexit` 钩子在退出时直接 `os._exit(0)`，
绕开这段会卡住的收尾流程。详见 `astock_toolkit/concurrency.py` 顶部注释。

## 需要你定期维护的配置（`astock_toolkit/config.py`）

- `CURRENT_MAINLINE`：当前市场主线板块（用于板块热度打分时加权，仅供参考）。
- `SECTOR_GROUPS`：四大关注板块对应的行业板块关键词，如果数据源又调整了板块命名
  导致匹配不上，来这里补关键词。
- 战法参数（首板回溯天数、底部均价窗口/倍数、回调买点区间、涨停判定阈值、
  MACD参数等）都在 `config.py` 里有注释说明，可按自己的偏好调整。

## 免责声明

本工具是个人选股逻辑的自动化实现，所有输出都是基于历史规则的信号提示，
**不构成投资建议**，最终买卖决策、仓位、风险控制完全由你自己判断和负责。
