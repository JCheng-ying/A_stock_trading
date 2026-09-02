# A股底部首板看板

个人使用的 A 股选股辅助工具，只做一件事：**每天扫描一次，找出"底部首板"信号，
追踪它们涨停后约5个交易日的回调情况**。纯只读展示，没有下单接口，也没有任何
"刷新/更新"按钮——所有数据更新都在 `daily_scan.py` 里，网页只负责展示结果。

## 选股逻辑

"底部首板"的判断只有两条：

1. 首板：此前30个交易日内没有涨停过（`FIRST_LIMIT_UP_LOOKBACK_DAYS`），排除
   连板/接力股，只要真正从底部启动的首板。
2. 底部：涨停当日收盘价，没有超过此前30个交易日的平均收盘价（`MA_PRICE_WINDOW`）——
   不看地量、不看均线是否走平钝化，就这一条价格判断。

然后：

3. 重点关注：第一个涨停板后约5个交易日内，回调到约5%左右（默认3%~7%区间）的股票。
4. 结合股票所属板块的当日热门度（涨停股池接口自带"所属行业"，另外还会尝试标注
   更精确的板块涨跌幅）。

范围：沪深主板 + 创业板 + 科创板，不含北交所、不含 ST。

下一步计划（暂未实现，先把前面这部分跑稳）：对已经回调到位的股票，结合 MACD
做进一步确认。

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
  两步的候选股票再做完整的历史校验。比全市场扫描快很多，但因为科创板要单独全量
  扫一遍，没有"只查候选名单"那么快。
  ```bash
  python daily_scan.py                        # 近7个交易日，含科创板（默认）
  python daily_scan.py --days 10               # 改成近10个交易日
  python daily_scan.py --skip-star-market       # 跳过科创板，只查涨停股池名单（更快，但不覆盖科创板）
  ```

- **全市场方案**：对沪深主板+创业板+科创板全市场逐只扫描（不依赖涨停股池接口，
  能覆盖到更早期、已经不在"近N日涨停"名单里但仍符合条件的历史信号）。第一次跑
  会比较慢（受限于数据源限速），建议先用 `--limit` 小范围测试。
  ```bash
  python daily_scan.py --full-universe --limit 300   # 先测试
  python daily_scan.py --full-universe                # 完整全市场扫描（收盘后跑，可能要跑很久）
  ```

可以用 cron / launchd 把 `python daily_scan.py` 设置成每天收盘后自动跑一次，例如
crontab：

```
30 15 * * 1-5 cd /Users/chengjiaying/Desktop/A_Stock_trading && .venv/bin/python daily_scan.py >> data/scan.log 2>&1
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

**以后每天更新**（这三步是核心，其余都是一次性的）：

```bash
source .venv/bin/activate
python daily_scan.py          # 1. 扫描，更新本地数据库
python generate_site.py       # 2. 根据数据库最新内容重新生成 index.html
git add -A && git commit -m "更新每日数据 $(date +%Y-%m-%d)" && git push   # 3. 推到 GitHub
```

推上去之后 GitHub Pages 会自动重新部署（一般一两分钟内生效），不需要在 Settings 里
再点任何东西。这三步已经存成了 `publish.sh`，第一次用要给它执行权限：

```bash
chmod +x publish.sh
./publish.sh
```

之后每天只要跑 `./publish.sh` 就行。如果想做到完全自动（不用自己手动跑），可以用
cron/launchd 定时跑这个脚本；也可以在 Claude Code 里用 `/schedule` 建一个云端定时
任务代劳，但如果 claude.ai 的 GitHub 连接器当时连不上（这是已知会遇到的问题，跟账号
权限无关），云端自动推送会失败，退回来手动跑或者本地 cron 更可靠。

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

## 需要你定期维护的配置（`astock_toolkit/config.py`）

- `CURRENT_MAINLINE`：当前市场主线板块（用于板块热度打分时加权，仅供参考）。
- `SECTOR_GROUPS`：四大关注板块对应的行业板块关键词，如果数据源又调整了板块命名
  导致匹配不上，来这里补关键词。
- 战法参数（首板回溯天数、底部均价窗口、回调买点区间、涨停判定阈值等）都在
  `config.py` 里有注释说明，可按自己的偏好调整。

## 免责声明

本工具是个人选股逻辑的自动化实现，所有输出都是基于历史规则的信号提示，
**不构成投资建议**，最终买卖决策、仓位、风险控制完全由你自己判断和负责。
