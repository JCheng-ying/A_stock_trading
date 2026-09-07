"""批量扫描股票代码的并发工具——用【多进程】而不是多线程。

这里有个坑：东方财富连不上时的新浪历史行情降级路径（data_source.get_daily_history
里的 stock_zh_a_daily）内部每次调用都会新建一个 py_mini_racer.MiniRacer()（跑一段
JS 来解密新浪的数据），而这东西底层是 V8，多个线程同时初始化会直接把整个进程崩掉
（"[FATAL:address_pool_manager.cc(67)] Check failed: !pool->IsInitialized()"，
不是 Python 异常，捕获不住，整个 daily_scan.py / scan_volume_surge.py 进程直接死掉）。
实测触发过，别再改回线程池。

改用多进程规避这个问题：每个子进程有自己独立的 V8/内存空间，互不干扰；瓶颈本来就是
网络I/O等待，多进程一样能拿到接近线性的加速。代价是子进程启动本身有点开销（重新
import akshare/pandas等），但这在整批任务里只是一次性成本。

用多进程有个要求：worker_fn 必须是"顶层函数"（模块级别定义，不能是闭包/lambda），
因为要跨进程传递就得能被 pickle 序列化。SQLite 那边已经在 db.py 里开了 WAL 模式 +
30秒 busy timeout，扛得住多个进程同时读写。

第二个坑（比上面那个更隐蔽，实测触发过）：子进程只要曾经走过新浪历史行情降级路径
（也就是内部建过一次 py_mini_racer.MiniRacer()），干完自己分到的活、正常返回准备
退出时会**卡死不退出**——用 `sample <pid>` 抓栈能看到卡在
`Py_Finalize → gc_collect_main → ThreadHandle_join → MiniRacer::IsolateManager::
PumpMessages`：mini_racer 内部起了一个不会自己停的 V8 消息泵后台线程，Python
解释器收尾阶段要 join 所有残留的非daemon线程，这个线程永远等不到东西，就永远
join 不完。子进程本身其实已经把结果传回主进程了（在这一步之前），只是这个进程
自己卡在退出的路上。要命的是 `ProcessPoolExecutor` 的 `shutdown(wait=True)`
（`with` 块退出时会调用）会等所有子进程真正退出，只要 8 个里有 1 个卡住，
整个扫描脚本就跟着假死，进度条哪怕已经跑满 100% 也永远不会往下走。

解决办法：给每个子进程注册一个 `atexit` 钩子，一旦要退出就直接 `os._exit(0)`，
跳过 Python 正常的解释器收尾（包括那次会卡住的线程join）。`atexit` 回调本身在
`Py_FinalizeEx` 里执行得足够早（早于后面才会卡住的 GC 收尾阶段），所以这样能
可靠地避开这个死锁；子进程的计算结果这时候已经传回主进程了，`os._exit` 不会
丢数据，代价只是跳过了一些多进程内部收尾的簿记（可能偶尔看到无害的
resource_tracker 泄漏警告，不影响正确性）。见下面 `_worker_process_init`。
"""

from __future__ import annotations

import concurrent.futures


def _worker_process_init():
    """ProcessPoolExecutor 的 initializer，在每个子进程刚启动时跑一次。

    注册一个 atexit 钩子，子进程退出时直接 os._exit(0)，绕开上面文档里说的
    mini_racer V8 后台线程导致的解释器收尾死锁。
    """
    import atexit
    import os
    atexit.register(os._exit, 0)


def run_concurrent(items, worker_fn, max_workers=8, progress_cb=None):
    """items: 待处理的列表（元素必须是可 pickle 的，比如 dict/tuple，不要用自定义对象）。
    worker_fn: 顶层函数（不能是闭包），签名 worker_fn(item) -> 命中结果(truthy) 或 None。

    返回所有命中结果组成的列表。max_workers<=1 时退化成顺序执行（调试/对比用，不开
    子进程）。
    """
    total = len(items)
    if total == 0:
        return []

    results = []
    done = 0

    if max_workers <= 1:
        for item in items:
            try:
                r = worker_fn(item)
            except Exception:  # noqa: BLE001
                r = None
            if r:
                results.append(r)
            done += 1
            if progress_cb:
                progress_cb(done, total)
        return results

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=max_workers, initializer=_worker_process_init
    ) as executor:
        futures = [executor.submit(worker_fn, item) for item in items]
        for future in concurrent.futures.as_completed(futures):
            try:
                r = future.result()
            except Exception:  # noqa: BLE001
                r = None
            if r:
                results.append(r)
            done += 1
            if progress_cb:
                progress_cb(done, total)
    return results
