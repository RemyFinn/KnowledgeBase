import os
import sys

from pathlib import Path
from app.core.logger import logger
from app.import_process.agent.state import ImportGraphState
from app.utils.task_utils import add_running_task


def node_entry(state: ImportGraphState) -> ImportGraphState:
    """
    节点: 入口节点 (node_entry)
    为什么叫这个名字: 作为图的 Entry Point，负责接收外部输入并决定流程走向。
    设计的state：local_file_path / is_read_md_enabled / is_read_pdf_enabled / md_path / pdf_path / file title
    未来要实现:
        日志输出:
            1.进入节点的日志输出【节点+核心参数】
                记录任务状态【那个任务开始了】 -> 给前端推送信息（埋点）
            2.参数校验（local_file_path -> 没有传入文件 -> end / local_dir -> 没有传入文件夹 -> 创建一个）
            3.解析文件类型，修改state对应的参数 local_file_path -> md | pdf
                -> is md_read_enabled True || is_pdf_read_enabled True
                -> md_path = local_file_path | pdf_path = local_file_path
                -> file_title = 读取文件名
            4.结束节点的日志输出【节点+核心参数】
                记录任务状态【哪个任务结束了】 -> 给前端推送信息（埋点）

    1. 接收文件路径。
    2. 判断文件类型 (PDF/MD)。
    3. 设置 state 中的路由标记 (is_pdf_read_enabled / is_md_read_enabled)。
    """

    # 1.进入节点的日志输出【节点 + 核心参数】 记录任务状态（给前端推送信息）
    function_name = sys._getframe().f_code.co_name
    # logger.info(f">>> [Stub] 执行节点: {sys._getframe().f_code.co_name}")
    logger.info(f">>> [{function_name}] 开始执行了！ 现在的状态为：{state}")
    add_running_task(state['task_id'], function_name)


    # 2.进行必要的非空校验
    local_file_path = state["local_file_path"]
    if not local_file_path:
        logger.error(f"[{function_name}]检查发现没有文件输入，无法继续解析！")
        return state

    # 3.判定并完成state属性赋值
    if local_file_path.endswith(".pdf"):
        # 处理pdf
        state["is_pdf_read_enabled"] = True
        state["pdf_path"] = local_file_path
    elif local_file_path.endswith(".md"):
        # 处理 md
        state["is_md_read_enabled"] = True
        state["md_path"] = local_file_path
    else:
        logger.error(f"[{function_name}]文件格式不是md,pdf，无法继续解析！")

    # 提取file_title /xx/xxx/aaa.pfg -> aaa -> 为了后期llm没有识别出来当前文件对应item_name -> file_title进行兜底
    # file_title_os = os.path.basename(local_file_path).split(".")[0]
    file_title = Path(local_file_path).stem # 去掉后缀的文件名 .name .suffix
    state["file_title"] = file_title

    # 4.结束节点的日志输出【节点 + 核心参数】 记录任务状态（给前端推送信息）
    function_name = sys._getframe().f_code.co_name
    logger.info(f">>> [{function_name}] 开始结束了！ 现在的状态为：{state}")
    add_running_task(state['task_id'], function_name)


    # 模拟简单的路由逻辑，防止报错 (仅 node_entry 需要)
    # if "local_file_path" in state:
    #     path = state["local_file_path"]
    #     if path.endswith(".pdf"):
    #         state["is_pdf_read_enabled"] = True
    #     elif path.endswith(".md"):
    #         state["is_md_read_enabled"] = True

    return state
