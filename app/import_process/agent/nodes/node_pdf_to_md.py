import os
import shutil
import sys
import zipfile

import requests
import time

from app.core.logger import logger
from app.import_process.agent.state import ImportGraphState, create_default_state
from app.utils.task_utils import add_running_task
from app.utils.path_util import PROJECT_ROOT
from pathlib import Path
from app.conf.mineru_config import MineruConfig, mineru_config


def step_1_validate_path(state):
    """
    路径校验  pdf_path 失效直接异常处理
             local_dir 没有，给与默认值
    为什么叫这个名字: 验证输入路径，并返回一个有效的路径对象。
    :param state:
    :return
    """
    logger.debug(f">>> [step_1_validate_path]在md转pdf下，开始进行文件格式校验！ 现在的状态为")
    # 常规非空校验（站在字符串的角度）
    # 进行文件存在校验
    pdf_path = state["pdf_path"]
    local_dir = state["local_dir"]
    if not pdf_path:
        logger.error(f"[step_1_validate_path]没有传入文件输入，无法继续解析！")
        raise ValueError("[step_1_validate_path]没有传入文件输入，无法继续解析！")
    if not local_dir:
        # 给与一个输出的默认值
        local_dir = str(PROJECT_ROOT / "output")
        logger.info(f"[step_1_validate_path]没有传入输出目录，已给与默认值：{local_dir}")

    pdf_path_obj = Path(pdf_path)
    local_dir_obj = Path(local_dir)

    if not pdf_path_obj.exists():
        logger.error(f"[step_1_validate_path]检查发现pdf_path不存在，请检查输入文件路径！")
        raise FileNotFoundError("[step_1_validate_path]检查发现pdf_path不存在，请检查输入文件路径！")
    if not local_dir_obj.exists():
        logger.error(f"[step_1_validate_path]检查发现local_dir不存在，主动创建目录！")
        local_dir_obj.mkdir(parents=True, exist_ok=True)

    return pdf_path_obj, local_dir_obj


def step_2_upload_and_poll(pdf_path_obj):
    """
        讲pdf文件用minerU解析，并且获取md对应的下载url地址
        :param pdf_path_obj: 上传解析pdf文件的path对象
        :return str: url ，minerU解析后md文件zip压缩包的下载地址
    """

    #  1.申请上传解析的地址
    # 前置准备和参数： url api | token | 准备一个固定格式的请求头
    token = mineru_config.api_key
    url = f"{mineru_config.base_url}/file-urls/batch"
    header = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    data = {
        "files": [
            {"name": f"{pdf_path_obj.name}"}
        ],
        "model_version": "vlm"
    }

    response = requests.post(url, headers=header, json=data)
    if response.status_code != 200 or response.json()['code'] != 0:
        logger.error(f"[step_2_upload_and_poll] 获取minerU解析接口地址失败，请检查输入文件路径！")
        raise RuntimeError(f"[step_2_upload_and_poll] 请求minerU解析接口失败，请检查输入文件路径！")
    uploaded_url = response.json()['data']['file_urls'][0]  # 上这个地址上传文件
    batch_id = response.json()['data']['batch_id']  # 处理id，后续根据这个id获取结果

    #  2.将文件上传到对应的解析地址
    # 使用Put请求，讲pdf_path_obj文件上传到uploaded_url地址即可
    # 注意： 不能直接使用Put，大概率会报错。 原因：电脑开了各种代理，put的请求头，添加一些额外的参数头，将文件真的转存在第三方文件存储服务器
    # 文件存储服务器都比较严格，拒绝存储，报错！get post 宽进宽出 put严进严出
    http_session = requests.Session()
    http_session.trust_env = False  # 1.禁止走代理 | 2.复用请求对象
    try:
        with open(pdf_path_obj, 'rb') as f:
            file_data = f.read()
        upload_response = http_session.put(uploaded_url, data=file_data)
        if upload_response.status_code != 200:
            logger.error(f"[step_2_upload_and_poll] 上传文件到minerU失败，请检查输入文件路径！")
            raise RuntimeError(f"[step_2_upload_and_poll] 上传文件到minerU失败，请检查输入文件路径！")
    except Exception as e:
        logger.error(f"[step_2_upload_and_poll] 上传文件到minerU失败，请检查输入文件路径！")
        raise RuntimeError(f"[step_2_upload_and_poll] 上传文件到minerU失败，请检查输入文件路径！")
    finally:
        http_session.close()
    #  3.轮询获取解析结果
    # 循环获取，确保获取到结果，再往后执行
    # 设计一个循环3-5秒获取一次，最多等待10分钟（600s） -> 600页pdf
    url = f"{mineru_config.base_url}/extract-results/batch/{batch_id}"  # "上一步批量提交返回的 batch_id"
    timeout_seconds = 600  # 官网 1s = 1页pdf
    poll_interval = 3  # 间隔时间是3s
    start_time = time.time()  # 进入的起始时间

    while True:
        # 3.1 超时判断 不能站在第一次角度，要站在宏观的角度
        if time.time() - start_time > timeout_seconds:
            logger.error(f"[step_2_upload_and_poll] 请求minerU解析接口超时，请检查输入文件路径！")
            raise RuntimeError(f"[step_2_upload_and_poll] 请求minerU解析接口超时，请检查输入文件路径！")

        # 3.2 向指定的url地址获取本次解析的结果
        res = requests.get(url, headers=header)

        # 3.3 解析结果判断和获取zip_url
        if res.status_code != 200:
            # 5xx 给与机会 直到timeout 超时
            # 抛出异常 1xxx 还是给机会 2xx 本次处理思路 => 333
            if 500 <= res.status_code < 600:
                time.sleep(poll_interval)
                continue
            raise RuntimeError(f"[step_2_upload_and_poll] minerU解析接口结果失败，返回的状态码为{res.status_code}！")

        json_data = res.json()  # 获取本次结果
        if json_data['code'] != 0:
            # != 0 大概率是token过期 或者 额度用尽等等
            raise RuntimeError(
                f"[step_2_upload_and_poll] minerU解析接口结果失败，返回的错误为： {json_data['code']}信息{json_data['msg']}！")

        # 判断解析状态
        extract_result = json_data['data']['extract_result'][0]
        if extract_result['state'] == 'done':
            # 解析完成可以获取结果
            full_zip_url = extract_result['full_zip_url']
            logger.info(
                f"[step_2_upload_and_poll] 解析完成，耗时：{time.time() - start_time}s，获取结果地址为：{full_zip_url}")
            return full_zip_url
        else:
            # 还没解析完
            time.sleep(poll_interval)


def step_3_download_and_extract(zip_url, local_dir_obj, stem) -> str:
    """
    下载指定的md.zip文件，并解压，返回解压后的md文件地址
    :param zip_url: 要下载的地址
    :param local_dir_obj: 存储的文件夹
    :param stem: pdf的文件名
    :return str: 返回md文件的地址
    """

    # 1.下载zip文件 response响应体
    response = requests.get(zip_url)

    if response.status_code != 200:
        logger.error(f"[step_3_download_and_extract] 下载文件失败，请检查输入文件路径！")
        raise RuntimeError(f"[step_3_download_and_extract] 下载文件失败，请检查输入文件路径！")

    # 2.把响应体的zip文件保存到本地
    zip_save_path = local_dir_obj / f"{stem}_result.zip"
    with open(zip_save_path, 'wb') as f:
        f.write(response.content)
    logger.info(f"[step_3_download_and_extract] 下载文件成功，保存路径为：{zip_save_path}")

    # 3.清空下旧目录（删除上一次处理的文件目录）
    # 先清空文件内容，因为两次解压的文件数量可能不一致，会保留旧数据
    extract_target_dir = local_dir_obj / stem
    if extract_target_dir.exists():
        # 递归进行目录内容删除/本身也会被删除
        shutil.rmtree(extract_target_dir)
    # 创建一个新目录
    extract_target_dir.mkdir(parents=True, exist_ok=True)

    # 4.解压zip文件
    # 对zip文件进行解压
    # zipfile.ZipFile(zip_save_path, mode='r') as zip_file_object 创建一个zip文件对象，只能读取，解压
    with zipfile.ZipFile(zip_save_path, mode='r') as zip_file_object:
        # 调用对象的extractall方法进行解压  参数: 解压到的目录  output / xxx
        zip_file_object.extractall(extract_target_dir)

    # 5.返回md文件的地址
    # 解压后得到文件的文件名可能叫 文件.md 低版本也可能叫 full.md
    md_file_list = list(extract_target_dir.rglob('*.md'))

    if not md_file_list:
        logger.error(f"[step_3_download_and_extract] 解压文件失败，请检查输入文件路径！")
        raise RuntimeError(f"[step_3_download_and_extract] 解压文件失败，请检查输入文件路径！")

    target_md_file = None # 存储最终的md文件

    # 检查有没有原文件名的md
    for md_file in md_file_list:
        # stem 文件名 xxx
        if md_file.name == stem + ".md":
            target_md_file = md_file
            break
    # 检查有没有full.md
    if not target_md_file:
        for md_file in md_file_list:
            # stem 文件名 xxx
            if md_file.name.lower() == "full.md":
                target_md_file = md_file
                break

    # 实在没有我就获取第一个就行
    if not target_md_file:
        target_md_file = md_file_list[0]

    # md文件名 xxx.md full.md 不知道.md
    # 统一改成 原文件名（stem）.md
    if target_md_file.stem != stem:
        # 进行重命名
        # target_md_file.with_name(stem + ".md") 修改path对象 （不涉及文件操作） 返回修改后的path对象
        # target_md_file.rename 修改磁盘中的文件名称 返回新的path
        target_md_file = target_md_file.rename(target_md_file.with_name(stem + ".md"))

    # 最终的md文件获取绝对路径，并且返回字符串类型
    final_md_str_path = str(target_md_file.resolve())
    logger.info(f"[step_3_download_and_extract] 完成md解压文件成功，保存路径为：{final_md_str_path}")
    return final_md_str_path

def node_pdf_to_md(state: ImportGraphState) -> ImportGraphState:
    """
    节点: PDF转Markdown (node_pdf_to_md)
    为什么叫这个名字: 核心任务是将 PDF 非结构化数据转换为 Markdown 结构化数据。
    未来要实现:
        日志输出:
            1.进入日志和任务状态的配置
            2.进行参数校验（local_dir -> 给与默认值 | local_file_path 完成字面意思的校验 -> 深入校验文件是否真的存在）
            3.调用minerU进行pdf解析（local_file_path）返回一个下载文件地址 xxx.zip url地址
            4.下载zip包，并解析和提取（local_dir）
            5.把md_path地址进行赋值，读取md的文件内容 md_content赋值（文本内容）
            6.结束的日志和任务状态的配置
            做容错处理！！！ try异常处理

    1. 调用 MinerU (magic-pdf) 工具。
    2. 将 PDF 转换成 Markdown 格式。
    3. 将结果保存到 state["md_content"]。
    """

    function_name = sys._getframe().f_code.co_name
    # logger.info(f">>> [Stub] 执行节点: {sys._getframe().f_code.co_name}")
    logger.info(f">>> [{function_name}] 开始执行了！ 现在的状态为：{state}")
    add_running_task(state['task_id'], function_name)

    try:
        # 2.进行参数校验（local_dir -> 给与默认值 | local_file_path 完成字面意思的校验 -> 深入校验文件是否真的存在）
        # 参数： state local_dir_path | local_dir
        # 返回：校验后的文件和文件夹Path对象
        # 返回校验完毕，可以直接使用的路径对象
        pdf_path_obj, local_dir_obj = step_1_validate_path(state)
        # 3.调用minerU进行pdf解析（local_file_path）返回一个下载文件地址 xxx.zip url地址
        # 参数：要解析的pdf文件路径 返回值：要下载的zip文件路径
        zip_url = step_2_upload_and_poll(pdf_path_obj)
        # 4.下载zip包，并解析和提取（local_dir）
        # 参数：1.要下载的地址 2.local_dir_obj 解压的文件夹 3. 文件名 提取的文件名
        # 返回值：解压后md 文件的真实路径
        md_path = step_3_download_and_extract(zip_url, local_dir_obj, pdf_path_obj.stem)
        # 5.把md_path地址进行赋值，读取md的文件内容 md_content赋值（文本内容）
        # 更新数据
        state["md_path"] = md_path
        state["local_dir"] = str(local_dir_obj)
        # md的内容读取，配置给md_content
        with open(md_path, "r", encoding="utf-8") as f:
            state["md_content"] = f.read()

    except Exception as e:
        # 处理异常
        logger.error(f"[{function_name}] 使用minerU解析发生异常，异常信息：{e}")
        raise  # 中指工作流
    finally:
        logger.info(f">>> [{function_name}] 开始结束了！ 现在的状态为：{state}")
        add_running_task(state['task_id'], function_name)

    return state


if __name__ == "__main__":
    # 单元测试：验证PDF转MD全流程
    logger.info("===== 开始node_pdf_to_md节点单元测试 =====")

    from app.utils.path_util import PROJECT_ROOT
    logger.info(f"测试获取根地址：{PROJECT_ROOT}")

    test_pdf_name = os.path.join("doc", "hak180产品安全手册.pdf")
    test_pdf_path = os.path.join(PROJECT_ROOT, test_pdf_name)

    # 构造测试状态
    test_state = create_default_state(
        task_id="test_pdf2md_task_001",
        pdf_path=test_pdf_path,
        local_dir=os.path.join(PROJECT_ROOT, "output")
    )

    node_pdf_to_md(test_state)

    logger.info("===== 结束node_pdf_to_md节点单元测试 =====")