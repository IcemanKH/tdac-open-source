# -*- coding: utf-8 -*-
"""
tdac_fill.py — 泰国电子入境卡（TDAC）本地半自动填写工具

设计原则
  * 只做"预填 + 回读校验"，绝不代替用户提交、绝不代替用户同意法律条款。
  * 不自带浏览器：启动用户本机已安装的 Chrome（--remote-debugging-port），
    再由 Playwright 通过 CDP 接管。这样 Cloudflare Turnstile 会正常放行。
  * 控件定位优先使用 formcontrolname / 标签 / 结构位置，不使用图像坐标点击。
  * 敏感信息（护照号、手机号）只从 DPAPI 加密文件读取，日志一律打码。

用法
  python tdac_fill.py --gui       启动简体中文图形界面（B1）
  python tdac_fill.py --check     仅做环境与配置检查（不打开浏览器）
  python tdac_fill.py --inspect   打开官网并导出当前表单的字段清单（排查用，会联网）
  python tdac_fill.py --dry-run   离线检查：完全合成资料，不启动浏览器、不触网
  python tdac_fill.py --smoke     同 --dry-run：完全合成资料，不启动浏览器、不触网
  python tdac_fill.py            正式流程（受 3 天申报窗口限制）
  附加开关：--keep-open  结束后保留浏览器窗口
           --session-info     只读打印会话/调试端口说明与残留检测
           --export-diagnostic [路径]  导出脱敏诊断包（B4）
           --check-update     经你同意后检查（可配置的）版本来源（B3）

安全边界
  * --smoke / --dry-run 一律离线：使用 tdac_synth 完全合成的资料，不读取真实配置、
    不启动浏览器、不访问泰国官网，因此不可能触发任何正式提交或虚假申报。
  * 只有正常正式流程（非 smoke/dry-run）才会打开官网，且始终停在预览页等待本人
    阅读条款、勾选并提交。当前版本**不提供**任何自动化的“官网交互测试模式”；
    若将来需要，必须另行单独授权并单独设计受控模式。
  * B3 版本检查只在用户明确发起时才联网，且只读版本号与官方下载页，不自动下载安装。
  * B4 诊断包只含白名单中的非敏感元信息，导出前自检，命中敏感模式即拒绝导出。
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import tdac_paths
import tdac_browser
from tdac_privacy import safe_value, NO_FULL_LOG_FIELDS

HERE = os.path.dirname(os.path.abspath(__file__))
# A2：程序与用户数据分离。以下目录均指向当前 Windows 用户的专属数据目录，
# 不再使用程序目录内的相对路径。
#
# G1 最终修复（P2）：改为**函数**动态解析路径。历史上这里是模块级常量，导入
# 时即冻结：一旦 TDAC_DATA_DIR 在导入之后被重定向（测试沙箱 / 便携场景），
# 日志、输出、配置就会写到旧目录，属于“写入路径不受保护”的隐患。
# 旧名字（CONFIG_DIR 等）通过模块级 __getattr__ 保留，外部引用不受影响。


def config_dir() -> str:
    return tdac_paths.config_dir()


def profile_dir() -> str:
    return tdac_paths.profile_dir()


def log_dir() -> str:
    return tdac_paths.log_dir()


def output_dir() -> str:
    return tdac_paths.output_dir()


def __getattr__(name: str):  # PEP 562：兼容旧代码里的 CONFIG_DIR / PROFILE_DIR 等常量用法
    legacy = {
        "CONFIG_DIR": config_dir,
        "PROFILE_DIR": profile_dir,
        "LOG_DIR": log_dir,
        "OUTPUT_DIR": output_dir,
    }
    if name in legacy:
        return legacy[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

TDAC_URL = "https://tdac.immigration.go.th/arrival-card/"

# B6：浏览器只使用系统**已安装**的 Chrome / Edge；检测与缺失引导见 tdac_browser。
# 保留旧名字以保证向后兼容。
CHROME_CANDIDATES = list(tdac_browser.CANDIDATES)
find_browser = tdac_browser.find_browser

# 申报窗口：抵达前 3 天内可申报
SUBMIT_WINDOW_DAYS = 3


# --------------------------------------------------------------------------- #
# B1：运行控制（GUI 暂停 / 停止；默认完全不启用，CLI 行为不变）
# --------------------------------------------------------------------------- #
class RunControl:
    """GUI 用来暂停 / 停止自动填写的协作式控制器。

    * 默认不安装（``_RUN_CONTROL is None``），因此 CLI 流程行为与旧版一致。
    * ``pause()`` / ``resume()`` 让自动流程在**安全检查点**处等待；
    * ``stop()`` 让自动流程在下一个安全检查点**优雅退出**，随后沿用既有的
      「保留浏览器页面供人工接管」路径，不会强行关闭浏览器。
    """

    def __init__(self) -> None:
        import threading
        self._resume = threading.Event()
        self._resume.set()          # set = 运行中
        self._stopped = False
        self.on_status = None       # Callable[[str], None]

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def stop(self) -> None:
        self._stopped = True
        self._resume.set()

    def tick(self, where: str = "") -> bool:
        """安全检查点：返回 False 表示应停止。暂停时会在此阻塞直到恢复。"""
        if self.on_status is not None:
            try:
                self.on_status(where)
            except Exception:
                pass
        self._resume.wait()
        return not self._stopped


_RUN_CONTROL: "Optional[RunControl]" = None


def set_run_control(control: "Optional[RunControl]") -> None:
    """安装 / 卸载运行控制器（仅 GUI 使用；传 None 即恢复默认行为）。"""
    global _RUN_CONTROL
    _RUN_CONTROL = control


def _tick(where: str = "") -> bool:
    ctrl = _RUN_CONTROL
    return True if ctrl is None else ctrl.tick(where)



# --------------------------------------------------------------------------- #
# 输出与日志（全部脱敏）
# --------------------------------------------------------------------------- #
class Rep:
    """统一的结果输出：PASS / FAIL / WARN / INFO，并记录到 logs/。"""

    def __init__(self) -> None:
        os.makedirs(log_dir(), exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.log_path = os.path.join(log_dir(), f"tdac-{stamp}.log")
        self._fh = open(self.log_path, "w", encoding="utf-8")
        self.failed = 0
        self.warned = 0

    def _w(self, line: str) -> None:
        # 官网 Material 图标文本偶尔包含 NBSP；Windows 常见 GBK 控制台
        # 无法直接输出该字符。日志仍保存原文，控制台仅在无法编码时替换。
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def ok(self, msg: str) -> None:
        self._w(f"  [PASS] {msg}")

    def fail(self, msg: str) -> None:
        self.failed += 1
        self._w(f"  [FAIL] {msg}")

    def warn(self, msg: str) -> None:
        self.warned += 1
        self._w(f"  [WARN] {msg}")

    def info(self, msg: str) -> None:
        self._w(f"        {msg}")

    def title(self, msg: str) -> None:
        self._w("")
        self._w(f"== {msg}")

    def close(self) -> None:
        # 幂等：重复关闭不会再次写已关闭的日志句柄（异常路径下由调用方兜底关闭）。
        if getattr(self, "_closed", False):
            return self.failed
        self._closed = True
        self._w("")
        self._w(f"日志文件：{self.log_path}")
        self._w(f"汇总：FAIL={self.failed}  WARN={self.warned}")
        self._fh.close()
        return self.failed


def mask(value: Any, head: int = 2, tail: int = 2) -> str:
    s = "" if value is None else str(value)
    if len(s) <= head + tail:
        return "*" * len(s)
    return f"{s[:head]}{'*' * (len(s) - head - tail)}{s[-tail:]}"


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def _read_json(name: str, required: bool = True) -> Dict[str, Any]:
    path = os.path.join(config_dir(), name)
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(f"缺少配置文件：{path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def smoke_trip_dates(today: Optional[dt.date] = None) -> Tuple[str, str]:
    """生成仅用于非提交演练的当日/次日虚构行程。"""
    arrival = today or dt.date.today()
    return arrival.strftime("%Y/%m/%d"), (arrival + dt.timedelta(days=7)).strftime("%Y/%m/%d")


def is_form_url(url: str) -> bool:
    return "/tac/arrival-card/add" in (url or "")


def is_preview_url(url: str) -> bool:
    return "/tac/arrival-card/preview" in (url or "")


def wait_route(page, predicate, timeout_ms: int = 15000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            if predicate(page.evaluate("() => location.href")):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def health_page_ready(page) -> bool:
    """健康步骤的 DOM 标志：国家 chip 输入和 Preview 按钮同时可见。"""
    try:
        chip = page.locator('input[data-testid="sit-autocomplete-input"]')
        preview = page.locator("button", has_text="Preview")
        return (chip.count() == 1 and chip.is_visible() and preview.count() > 0
                and preview.first.is_visible())
    except Exception:
        return False


def preview_page_ready(page) -> bool:
    """预览组件复用 add 路由，不能只靠 URL 判断。"""
    try:
        body = page.locator("body").inner_text(timeout=1000)
        return ("Please verify the accuracy of all information before submitting" in body
                and page.locator("button", has_text="Submit").count() > 0)
    except Exception:
        return False


def wait_page_ready(page, predicate, timeout_ms: int = 20000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if predicate(page):
            return True
        time.sleep(0.25)
    return False


def health_chip_values(page) -> List[str]:
    try:
        return page.locator("mat-chip-row").all_inner_texts()
    except Exception:
        return []


def health_answer(dynamic: Dict[str, Any], key: str) -> Optional[str]:
    """A6：返回健康答案；缺值返回 None。

    调用方必须据此停止，**严禁**用 "NO" 或任何默认答案代替缺失值。
    """
    val = dynamic.get(key)
    if val is None or str(val).strip() == "":
        return None
    return str(val).strip().upper()


def fill_health_radio(page, formcontrolname: str, answer: str, rep: Rep) -> bool:
    """填写条件出现的健康单选项；只接受 YES/NO，不猜测其它答案。"""
    group = page.locator(f'mat-radio-group[formcontrolname="{formcontrolname}"]')
    if group.count() == 0 or not group.first.is_visible():
        return True  # 条件控件未出现（例如中国路径的黄热病区块）
    wanted = answer.strip().upper()
    if wanted not in ("YES", "NO"):
        rep.fail(f"健康控件 {formcontrolname} 的答案必须是 YES/NO")
        return False
    buttons = group.first.locator("mat-radio-button")
    for i in range(buttons.count()):
        label = buttons.nth(i).inner_text().strip().upper()
        if label == wanted or wanted in label.split():
            buttons.nth(i).evaluate("e => { e.click(); return true; }")
            rep.ok(f"健康项 {formcontrolname}：{wanted}")
            return True
    rep.fail(f"健康控件 {formcontrolname} 未找到 {wanted} 选项")
    return False


def extract_preview_fields(page) -> Dict[str, str]:
    """从预览页的可见文本映射非敏感字段；不返回护照号、电话或邮箱。"""
    import re
    lines = [re.sub(r"\s+", " ", x).strip() for x in page.locator("body").inner_text().splitlines()]
    lines = [x for x in lines if x]

    def values_after(label: str) -> List[str]:
        out: List[str] = []
        for i, line in enumerate(lines):
            if line == label and i + 1 < len(lines):
                out.append(lines[i + 1])
        return out

    def first(label: str) -> str:
        vals = values_after(label)
        return vals[0] if vals else ""

    mode = values_after("Mode of Travel :")
    flight = values_after("Flight No./Vehicle No. :")
    transport = values_after("Mode of Transport :")
    return {
        "arrivalDate": first("Date of Arrival :"),
        "departureDate": first("Date of Departure :"),
        "countryOfBoarding": first("Country/Territory where you Boarded :"),
        "purposeOfTravel": first("Purpose of Travel :"),
        "arrivalMode": mode[0] if len(mode) > 0 else "",
        "departureMode": mode[1] if len(mode) > 1 else "",
        "arrivalFlightType": transport[0] if len(transport) > 0 else "",
        "departureFlightType": transport[1] if len(transport) > 1 else "",
        "arrivalFlightNo": flight[0] if len(flight) > 0 else "",
        "departureFlightNo": flight[1] if len(flight) > 1 else "",
        "accommodationType": first("Type of Accommodation in Thailand :"),
        "accommodationProvince": first("Province :"),
        "accommodationDistrict": first("District, Area :"),
        "accommodationSubdistrict": first("Sub-District, Sub-Area :"),
        "accommodationPostalCode": first("Post Code :"),
        "accommodationAddress": first("Address :"),
    }


def export_preview_pdf(page, output_dir: Optional[str] = None, stamp: Optional[str] = None,
                       path: Optional[str] = None) -> str:
    """用当前 Chromium 页面导出预览 PDF，不点击条款或 Submit。

    A3：本函数**只应在用户主动选择导出时被调用**；程序默认不会生成任何 PDF。
    - path 提供时，严格使用用户指定的保存位置；
    - 否则回退到 output_dir + 时间戳命名。
    """
    if path:
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    else:
        output_dir = output_dir or tdac_paths.output_dir()
        os.makedirs(output_dir, exist_ok=True)
        stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(output_dir, f"TDAC_预览_{stamp}.pdf")
    # Preview 切换后官网可能短暂保留遮罩/进度控件；等待它们消失，避免把
    # 灰色加载层打印进交付给同行人复核的 PDF。测试替身没有这些方法时继续打印。
    try:
        page.wait_for_timeout(800)
        page.wait_for_function(
            """() => Array.from(document.querySelectorAll(
                '.cdk-overlay-backdrop, mat-progress-spinner, mat-spinner, .mat-mdc-progress-spinner'
            )).every(e => {
                const s = getComputedStyle(e);
                return s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0;
            })""",
            timeout=5000,
        )
    except Exception:
        pass
    expand_script = """() => {
      const targets = [document.documentElement, document.body,
        document.querySelector('.sit-container')].filter(Boolean);
      for (const el of targets) {
        el.setAttribute('data-tdac-print-style', el.getAttribute('style') || '');
        el.style.setProperty('height', 'auto', 'important');
        el.style.setProperty('min-height', '0', 'important');
        el.style.setProperty('max-height', 'none', 'important');
        el.style.setProperty('overflow', 'visible', 'important');
        el.style.setProperty('overflow-y', 'visible', 'important');
      }
      window.scrollTo(0, 0);
      return targets.length;
    }"""
    restore_script = """() => {
      const targets = [document.documentElement, document.body,
        document.querySelector('.sit-container')].filter(Boolean);
      for (const el of targets) {
        const previous = el.getAttribute('data-tdac-print-style');
        if (previous) el.setAttribute('style', previous);
        else el.removeAttribute('style');
        el.removeAttribute('data-tdac-print-style');
      }
      return true;
    }"""
    try:
        page.evaluate(expand_script)
        try:
            page.wait_for_timeout(100)
        except Exception:
            pass
        cdp = page.context.new_cdp_session(page)
        result = cdp.send("Page.printToPDF", {
            "printBackground": True,
            "preferCSSPageSize": True,
            "displayHeaderFooter": False,
            "paperWidth": 8.27,
            "paperHeight": 11.69,
            "marginTop": 0.25,
            "marginBottom": 0.25,
            "marginLeft": 0.25,
            "marginRight": 0.25,
        })
    finally:
        try:
            page.evaluate(restore_script)
        except Exception:
            pass
    data = result.get("data") if isinstance(result, dict) else None
    if not data:
        raise RuntimeError("Chromium 未返回 PDF 数据")
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(data))
    if os.path.getsize(path) == 0:
        raise RuntimeError("PDF 文件为空")
    return path


def required_fields_rows() -> List[Dict[str, str]]:
    """返回给同行人填写的必填/人工确认字段清单，不含任何真实个人资料。"""
    return [
        {"分区": "个人信息", "字段键": "familyName", "字段": "英文姓", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "按护照英文姓名", "说明": "必须与护照一致"},
        {"分区": "个人信息", "字段键": "firstName", "字段": "英文名", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "按护照英文姓名", "说明": "必须与护照一致"},
        {"分区": "个人信息", "字段键": "middleName", "字段": "中间名", "是否必填": "否", "填写方式": "朋友填写", "填写示例": "没有则留空", "说明": "护照有中间名才填写"},
        {"分区": "个人信息", "字段键": "passportNo", "字段": "护照号", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "按护照填写", "说明": "请仔细核对 0/O、1/I 等字符"},
        {"分区": "个人信息", "字段键": "nationality", "字段": "国籍", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "CHINESE", "说明": "按护照国籍填写"},
        {"分区": "个人信息", "字段键": "dateOfBirth", "字段": "出生日期", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "YYYY/MM/DD", "说明": "按护照填写"},
        {"分区": "个人信息", "字段键": "occupation", "字段": "职业", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "EMPLOYEE / STUDENT", "说明": "使用官网可识别的英文选项"},
        {"分区": "个人信息", "字段键": "gender", "字段": "性别", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "MALE / FEMALE", "说明": "按实际情况填写"},
        {"分区": "个人信息", "字段键": "visaNo", "字段": "签证号", "是否必填": "否", "填写方式": "朋友填写", "填写示例": "免签则留空", "说明": "有签证才填写"},
        {"分区": "个人信息", "字段键": "countryOfResidence", "字段": "居住国家", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "CHINA", "说明": "按实际居住国家填写"},
        {"分区": "个人信息", "字段键": "cityOfResidence", "字段": "居住城市", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "居住城市名称", "说明": "按实际居住城市填写"},
        {"分区": "个人信息", "字段键": "phoneCode", "字段": "电话区号", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "86", "说明": "只填数字，不加 +"},
        {"分区": "个人信息", "字段键": "phoneNo", "字段": "手机号", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "手机号", "说明": "填写可接收联系的号码"},
        {"分区": "行程", "字段键": "arrivalDate", "字段": "入境日期", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "YYYY/MM/DD", "说明": "按机票填写"},
        {"分区": "行程", "字段键": "countryOfBoarding", "字段": "出发/登机国家", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "CHINA", "说明": "最后登机的国家/地区"},
        {"分区": "行程", "字段键": "purposeOfTravel", "字段": "旅行目的", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "HOLIDAY", "说明": "按官网选项填写"},
        {"分区": "行程", "字段键": "arrivalMode", "字段": "入境交通方式", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "AIR", "说明": "AIR / LAND / SEA"},
        {"分区": "行程", "字段键": "arrivalFlightType", "字段": "入境交通类型", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "COMMERCIAL FLIGHT", "说明": "按实际交通类型填写"},
        {"分区": "行程", "字段键": "arrivalFlightNo", "字段": "入境航班/车船号", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "按票面填写", "说明": "按机票/车票/船票填写"},
        {"分区": "行程", "字段键": "departureDate", "字段": "离境日期", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "YYYY/MM/DD", "说明": "按返程票填写"},
        {"分区": "行程", "字段键": "departureMode", "字段": "离境交通方式", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "AIR", "说明": "AIR / LAND / SEA"},
        {"分区": "行程", "字段键": "departureFlightType", "字段": "离境交通类型", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "COMMERCIAL FLIGHT", "说明": "按实际交通类型填写"},
        {"分区": "行程", "字段键": "departureFlightNo", "字段": "离境航班/车船号", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "按票面填写", "说明": "按机票/车票/船票填写"},
        {"分区": "住宿", "字段键": "accommodationType", "字段": "住宿类型", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "HOTEL", "说明": "按实际住宿类型填写"},
        {"分区": "住宿", "字段键": "accommodationProvince", "字段": "府/省", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "府/省名称", "说明": "按住宿地点选择"},
        {"分区": "住宿", "字段键": "accommodationDistrict", "字段": "区/县", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "区/县名称", "说明": "按住宿地点选择"},
        {"分区": "住宿", "字段键": "accommodationSubdistrict", "字段": "分区", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "分区名称", "说明": "按住宿地点选择"},
        {"分区": "住宿", "字段键": "accommodationPostalCode", "字段": "邮编", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "邮编", "说明": "按住宿地址填写"},
        {"分区": "住宿", "字段键": "accommodationAddress", "字段": "详细地址", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "酒店完整地址", "说明": "建议与订单/酒店确认单一致"},
        {"分区": "健康声明", "字段键": "last14DaysCountries", "字段": "过去14天停留国家/地区", "是否必填": "是", "填写方式": "朋友填写", "填写示例": "CHINA", "说明": "按实际行程填写，可多个"},
        {"分区": "健康声明", "字段键": "yellowFeverVaccination", "字段": "黄热病疫苗/风险行程", "是否必填": "按页面显示", "填写方式": "朋友填写", "填写示例": "NO / YES", "说明": "只按实际情况回答"},
        {"分区": "健康声明", "字段键": "symptoms", "字段": "症状申报", "是否必填": "按页面显示", "填写方式": "朋友填写", "填写示例": "NO / YES", "说明": "只按实际情况回答"},
        {"分区": "健康声明", "字段键": "termsAcceptedByUser", "字段": "条款确认", "是否必填": "最终提交前", "填写方式": "本人手动", "填写示例": "阅读后勾选", "说明": "脚本不会勾选或提交"},
    ]


def write_required_fields_csv(output_dir: Optional[str] = None, stamp: Optional[str] = None) -> str:
    """写出无真实资料的同行人必填项 CSV，Excel 可直接打开。"""
    output_dir = output_dir or tdac_paths.output_dir()
    os.makedirs(output_dir, exist_ok=True)
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(output_dir, f"TDAC_同行人必填信息表_{stamp}.csv")
    rows = required_fields_rows()
    fieldnames = ["分区", "字段键", "字段", "是否必填", "填写方式", "填写示例", "说明"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


# --------------------------------------------------------------------------- #
# B5：预览页收件邮箱字段处理（复用邮箱格式校验；识别不到就暂停转人工）
# --------------------------------------------------------------------------- #
_PREVIEW_EMAIL_SELECTORS = (
    'input[formcontrolname="email"]',
    'input[type="email"]',
    'input[data-tdac="tdac-email"]',
    'mat-form-field input[formcontrolname="receiverEmail"]',
)


def preview_email_control(page):
    """尽力识别预览页**可编辑**的收件邮箱控件；识别不到返回 None（绝不猜测）。"""
    for sel in _PREVIEW_EMAIL_SELECTORS:
        try:
            loc = page.locator(sel)
            n = loc.count()
        except Exception:
            continue
        for i in range(n):
            try:
                el = loc.nth(i)
                if el.is_visible():
                    # 只接受可编辑控件；只读展示不在这里改。
                    try:
                        if not el.is_editable():
                            continue
                    except Exception:
                        pass
                    return el
            except Exception:
                continue
    return None


def fill_preview_email(page, email: str, rep: Rep) -> Optional[bool]:
    """在预览页填写并回读收件邮箱。

    返回：
      * True  —— 写入并回读一致；
      * False —— 邮箱格式本身不合法（配置问题，需修正）；
      * None  —— 未能可靠识别控件、或写入/回读不可靠：**暂停自动处理**，
                 由用户在该页人工填写/核对（不算失败）。
    """
    from tdac_validate import validate_email
    ok, msg = validate_email(email)
    if not ok:
        rep.fail("收件邮箱格式不正确：" + msg)
        return False
    el = preview_email_control(page)
    if el is None:
        rep.warn("未能可靠识别预览页的收件邮箱控件：已暂停自动填写，请在预览页人工填写并核对。")
        return None
    try:
        el.click()
        el.fill("")
        el.type(email, delay=20)
        el.press("Tab")
    except Exception as e:
        rep.warn(f"收件邮箱写入失败（{type(e).__name__}）：请在预览页人工填写并核对。")
        return None
    try:
        got = (el.input_value() or "").strip()
    except Exception:
        got = ""
    if got and got.upper() == email.strip().upper():
        rep.ok(f"收件邮箱：已填写并回读一致（{safe_value('email', email)}）")
        return True
    rep.warn("收件邮箱回读不一致：请在预览页人工核对后再提交。")
    return None


def verify_preview(page, cfg: Dict[str, Any], rep: Rep) -> bool:
    """输出脱敏预览核对清单，并确认条款仍未被勾选。"""
    if not wait_page_ready(page, preview_page_ready):
        rep.fail("未能确认官网预览组件")
        return False
    actual = extract_preview_fields(page)
    trip = cfg["trip"]
    expected = {
        "arrivalDate": trip.get("arrivalDate", ""),
        "departureDate": trip.get("departureDate", ""),
        "countryOfBoarding": trip.get("countryOfBoarding", ""),
        "purposeOfTravel": trip.get("purposeOfTravel", ""),
        "arrivalMode": trip.get("arrivalMode", ""),
        "departureMode": trip.get("departureMode", ""),
        "arrivalFlightType": trip.get("arrivalFlightType", ""),
        "departureFlightType": trip.get("departureFlightType", ""),
        "arrivalFlightNo": trip.get("arrivalFlightNo", ""),
        "departureFlightNo": trip.get("departureFlightNo", ""),
        "accommodationType": trip.get("accommodationType", ""),
        "accommodationProvince": trip.get("accommodationProvince", ""),
        "accommodationDistrict": trip.get("accommodationDistrict", ""),
        "accommodationSubdistrict": trip.get("accommodationSubdistrict", ""),
        "accommodationPostalCode": trip.get("accommodationPostalCode", ""),
        "accommodationAddress": trip.get("accommodationAddress", ""),
    }
    labels = {
        "arrivalDate": "入境日期", "departureDate": "离境日期", "countryOfBoarding": "登机国家/地区",
        "purposeOfTravel": "旅行目的", "arrivalMode": "入境方式", "departureMode": "离境方式",
        "arrivalFlightType": "入境交通类型", "departureFlightType": "离境交通类型",
        "arrivalFlightNo": "入境航班号", "departureFlightNo": "离境航班号",
        "accommodationType": "住宿类型", "accommodationProvince": "府/省", "accommodationDistrict": "区/县",
        "accommodationSubdistrict": "分区", "accommodationPostalCode": "邮编", "accommodationAddress": "住宿地址",
    }
    rep.title("8. 预览核对清单（脱敏）")
    all_ok = True
    for key, exp in expected.items():
        got = actual.get(key, "")
        # 住宿地址可能含大小写/空格格式差异，统一比较；日志不回显地址正文。
        if key == "countryOfBoarding":
            target = str(exp).strip().upper()
            alias = COUNTRY_ALIASES.get(target, target)
            same = alias in got.upper() or target in got.upper()
        else:
            same = got.strip().upper() == str(exp).strip().upper()
        if same:
            # A8：日志不写入完整地址 / 航班号 / 邮编等；由 safe_value 统一处理。
            rep.ok(f"{labels[key]}：{safe_value(key, got)}")
        else:
            all_ok = False
            rep.fail(f"{labels[key]}：页面值与配置不一致（页面值 {safe_value(key, got)}）")
    # B5：预览页收件邮箱（识别不到即暂停自动处理，转人工填写）
    email = str((cfg.get("profile") or {}).get("email", "")).strip()
    if email:
        if fill_preview_email(page, email, rep) is False:
            all_ok = False
    else:
        rep.warn("未配置收件邮箱：已跳过预览页邮箱自动填写，请人工填写。")

    terms = page.locator('input[type="checkbox"]')
    checked = any(terms.nth(i).is_checked() for i in range(terms.count()))
    if checked:
        rep.fail("检测到条款复选框已勾选，自动流程拒绝继续")
        all_ok = False
    else:
        rep.ok("条款复选框保持未勾选")
    rep.info("当前停留在预览页；Submit 与条款均由你本人手动操作。")
    return all_ok


def click_enabled_button(page, text: str, timeout_ms: int = 15000) -> bool:
    """点击当前可见且启用的同名按钮，避免命中隐藏步骤模板。"""
    active = page.locator("mat-step-content[aria-expanded='true'] button", has_text=text)
    buttons = active if active.count() else page.locator("button", has_text=text)
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for i in range(buttons.count()):
            btn = buttons.nth(i)
            try:
                if btn.is_visible() and btn.is_enabled():
                    btn.click(timeout=3000)
                    return True
            except Exception:
                continue
        time.sleep(0.25)
    return False


def advance_health_and_preview(page, cfg: Dict[str, Any], rep: Rep) -> bool:
    """等待并自动填写健康页，然后停在预览；不触碰条款或 Submit。"""
    rep.title("7. 健康声明")
    if not wait_route(page, lambda u: "/tac/arrival-card/add" in u):
        rep.fail("未能确认仍在 TDAC 表单路由，停止健康步骤")
        return False
    dynamic = cfg.get("dynamic", {})
    if not dynamic.get("last14DaysConfirmed", False) or not dynamic.get("healthDeclarationConfirmed", False):
        rep.fail("健康答案未在 config/dynamic.json 标记 confirmed，已停止而不弹出运行时确认")
        return False
    if not wait_page_ready(page, health_page_ready, timeout_ms=20000):
        rep.fail("第二阶段 Continue 后未进入健康声明页")
        return False
    countries = dynamic.get("last14DaysCountries", [])
    if not isinstance(countries, list) or not countries:
        rep.fail("健康页缺少过去 14 天国家/地区配置")
        return False
    chip_input = page.locator('input[data-testid="sit-autocomplete-input"]').first
    for country in countries:
        target = str(country).strip()
        if not target:
            continue
        alias = COUNTRY_ALIASES.get(target.upper(), target)
        chip_text = " ".join(health_chip_values(page)).upper()
        if alias.upper() in chip_text or target.upper() in chip_text:
            continue
        chip_input.click()
        chip_input.fill(alias)
        if not pick_from_panel(page, target, timeout=10000, rep=rep):
            rep.fail(f"健康页国家/地区未找到：{target}")
            return False
        time.sleep(0.3)
    if not health_chip_values(page):
        rep.fail("健康页国家/地区未成功写入")
        return False
    rep.ok("过去 14 天停留国家/地区：已按配置写入并回读")
    for control, label, key in (
        ("yellowFever", "黄热病疫苗", "yellowFeverVaccination"),
        ("symptoms", "症状申报", "symptoms"),
    ):
        group = page.locator(f'mat-radio-group[formcontrolname="{control}"]')
        if group.count() == 0 or not group.first.is_visible():
            rep.info(f"{label}：当前国家路径未显示额外控件，按官网规则无需填写")
            continue
        answer = health_answer(dynamic, key)
        if answer is None:
            # A6：控件已出现但配置缺值 —— 立即停止，绝不采用默认答案。
            rep.fail(f"{label}：配置 {key} 缺值，已停止而不采用任何默认答案")
            return False
        if answer not in ("YES", "NO"):
            rep.fail(f"{label}：配置 {key} 必须是 YES/NO，当前为「{answer}」")
            return False
        if not fill_health_radio(page, control, answer, rep):
            return False
    if not click_enabled_button(page, "Preview"):
        rep.fail("健康页未找到可见且启用的 Preview 按钮")
        return False
    return verify_preview(page, cfg, rep)


def date_keystrokes(value: str) -> str:
    """官网日期掩码会自行插入斜线，键入时只发送八位数字。"""
    return "".join(ch for ch in value if ch.isdigit())


def load_config(smoke: bool = False) -> Dict[str, Any]:
    """加载配置。

    A5：smoke=True 时使用**完全合成**的资料，绝不读取真实 config/，
    也不在真实资料上做"逐键覆盖"。返回的 synthetic=True 便于下游断言。
    """
    if smoke:
        from tdac_synth import synthetic_config
        return synthetic_config()
    profile = _read_json("profile.json")
    trip = _read_json("trip.json")
    dynamic = _read_json("dynamic.json", required=False)
    return {"profile": profile, "trip": trip, "dynamic": dynamic,
            "smoke": False, "synthetic": False}


def check_config(cfg: Dict[str, Any], rep: Rep) -> List[str]:
    """返回缺失的关键字段列表。"""
    missing: List[str] = []
    p, t = cfg["profile"], cfg["trip"]
    need_p = ["familyName", "firstName", "nationality", "dateOfBirth", "gender",
              "occupation", "countryOfResidence", "cityOfResidence", "email",
              "phoneCode", "passportNo", "phoneNo"]
    need_t = ["arrivalDate", "departureDate", "countryOfBoarding", "purposeOfTravel",
              "arrivalMode", "arrivalFlightType", "arrivalFlightNo",
              "departureMode", "departureFlightType", "departureFlightNo",
              "accommodationType", "accommodationProvince", "accommodationDistrict",
              "accommodationSubdistrict", "accommodationPostalCode", "accommodationAddress"]
    for k in need_p:
        if not str(p.get(k, "")).strip():
            if k in ("middleName", "visaNo"):
                continue  # 允许留空
            missing.append(f"profile.{k}")
    for k in need_t:
        if not str(t.get(k, "")).strip():
            missing.append(f"trip.{k}")
    return missing


def check_window(trip: Dict[str, Any]) -> Tuple[bool, str]:
    arrival = dt.datetime.strptime(trip["arrivalDate"], "%Y/%m/%d").date()
    today = dt.date.today()
    start = arrival - dt.timedelta(days=SUBMIT_WINDOW_DAYS)
    if today < start:
        return False, (f"尚未进入申报窗口：最早可申报 {start.isoformat()}（抵达 {arrival.isoformat()}），"
                       f"今天是 {today.isoformat()}，还需等待 {(start - today).days} 天。")
    if today > arrival:
        return False, f"抵达日期 {arrival.isoformat()} 已过（今天 {today.isoformat()}）。"
    return True, f"处于申报窗口内（{start.isoformat()} ~ {arrival.isoformat()}）。"


# --------------------------------------------------------------------------- #
# 环境（B6：浏览器检测与缺失引导；不改任何浏览器启动参数）
# --------------------------------------------------------------------------- #
def venv_python() -> str:
    return os.path.join(HERE, ".build", "venv", "Scripts", "python.exe")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def preflight_env(rep: Rep) -> Tuple[Optional[str], int]:
    rep.title("1. 环境检查")
    kind, browser = tdac_browser.detect_browser()
    if browser:
        rep.ok(f"浏览器：{kind}（{browser}）")
    else:
        # B6：缺失时给出中文说明与官方下载入口，不自动安装、不改默认浏览器。
        rep.fail("未找到 Chrome / Edge")
        for line in tdac_browser.missing_browser_help().splitlines():
            rep.info(line)

    vpy = venv_python()
    if os.path.exists(vpy):
        rep.ok(f"虚拟环境：{vpy}")
    else:
        vpy = sys.executable
        rep.warn(f"未找到项目 venv，改用当前解释器：{vpy}")
    try:
        import playwright  # noqa: F401
        rep.ok("Playwright 已安装（Python 包）")
    except ImportError:
        rep.fail("Playwright 未安装。请运行：.build\\venv\\Scripts\\python.exe -m pip install playwright")
        browser = None

    port = free_port()
    rep.info(f"CDP 调试端口：{port}（仅绑定回环 127.0.0.1，随机分配）")
    return browser, port


# --------------------------------------------------------------------------- #
# 浏览器（B7：会话与调试端口）
# --------------------------------------------------------------------------- #
def report_session(rep: Rep) -> None:
    """只读地报告会话残留情况（不杀进程、不删文件）。"""
    info = tdac_browser.detect_leftovers(profile_dir())
    rep.info("独立用户目录：" + profile_dir())
    rep.info(tdac_browser.describe_leftovers(info))


def _close_stale_profile_browser(rep: Rep) -> None:
    """清理仍占用本工具**专用** browser_profile 的残留 Chrome / Edge。

    只按命令行里精确包含本工具专用 profile 目录来筛选，因此**绝不会**动用户
    日常使用的浏览器（不匹配就一个字都不碰）。清理属于必要动作，否则新实例会
    因 profile 被占用而启动失败；检测本身是只读的。
    """
    pids = tdac_browser.list_stale_processes(profile_dir())
    if not pids:
        return
    rep.warn(f"发现 {len(pids)} 个占用专用 profile 的残留浏览器进程，正在清理…")
    import subprocess as sp
    for pid in pids:
        try:
            sp.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, timeout=15, check=False)
        except Exception:
            pass
    time.sleep(2)


def launch_browser(binary: str, port: int, rep: Rep) -> subprocess.Popen:
    os.makedirs(profile_dir(), exist_ok=True)
    report_session(rep)
    _close_stale_profile_browser(rep)
    # 启动参数保持不变：仅随机回环调试端口 + 专用用户目录；
    # 不使用 --remote-debugging-address / --remote-allow-origins 等会扩大访问范围的参数。
    args = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--start-maximized",
        "about:blank",
    ]
    # B7 硬校验：绝不使用会扩大远程调试访问范围的参数。
    tdac_browser.assert_safe_switches(args)
    rep.info("启动浏览器（独立用户目录，不影响你日常用的 Chrome）")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = f"http://127.0.0.1:{port}/json/version"
    import urllib.request
    for _ in range(80):
        try:
            with urllib.request.urlopen(ws, timeout=1) as r:
                if r.status == 200:
                    rep.ok("浏览器已就绪，CDP 可用（回环调试端口）")
                    return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(
        "浏览器启动超时：CDP 端口未就绪。\n"
        "常见原因：已有另一个实例占用了专用用户目录。\n"
        "处理：关闭所有由本工具打开的浏览器窗口后重试。"
    )


# 角色绑定：把易变的 DOM 结构收敛成稳定的 data-tdac 属性。
# 表单结构变化时，通常只需要修这一处。
BIND_JS = r"""
() => {
  const FFS = () => Array.from(document.querySelectorAll('mat-form-field'));
  const AUTO = 'input.mat-mdc-autocomplete-trigger:not([type=hidden])';
  // 步骤切换或页面动态渲染后会再次执行本脚本。先清理旧标记，避免某个
  // 原本唯一、后来变成重复的控件仍带着上一次的角色而被误用。
  document.querySelectorAll('[data-tdac^="tdac-"]').forEach(
    el => el.removeAttribute('data-tdac'));
  const bind = (el, role) => { if (el) el.setAttribute('data-tdac', role); return !!el; };
  const one = (selector) => {
    const matches = Array.from(document.querySelectorAll(selector));
    const count = matches.length;
    if (count !== 1) return null;
    return matches[0];
  };

  // 1) 有稳定 formcontrolname 的控件，直接绑定
  const byFc = {
    familyName:'familyName', firstName:'firstName', middleName:'middleName',
    passportNo:'passportNo', occupation:'occupation', visaNo:'visaNo',
    phoneCode:'phoneCode', phoneNo:'phoneNo',
    arrDate:'arrDate', flightNo:'arrivalFlightNo', deptDate:'departureDate',
    deptFlightNo:'departureFlightNo', accPostCode:'accPostalCode', accAddress:'accAddress'
  };
  const out = {};
  for (const [fc, role] of Object.entries(byFc)) {
    out[role] = bind(one(`[formcontrolname="${fc}"]`), 'tdac-' + role);
  }
  for (const [fc, role] of Object.entries({
    traPurposeId:'purposeOfTravel', tranModeId:'arrivalFlightType',
    deptTranModeId:'departureFlightType', accTypeId:'accommodationType'
  })) {
    out[role] = bind(one(`mat-select[formcontrolname="${fc}"]`), 'tdac-' + role);
  }
  for (const [fc, role] of Object.entries({
    gender:'gender', traModeId:'arrivalMode', deptTraModeId:'departureMode'
  })) {
    out[role] = bind(one(`mat-radio-group[formcontrolname="${fc}"]`), 'tdac-' + role);
  }

  // 2) 自动完成框：以某个已知控件为锚点，向后按顺序取 N 个
  const after = (anchorFc, n) => {
    const anchor = one(`[formcontrolname="${anchorFc}"]`);
    if (!anchor) return [];
    const anchorFf = anchor.closest('mat-form-field');
    const ffs = FFS();
    let start = ffs.indexOf(anchorFf);
    if (start < 0) return [];
    const res = [];
    for (let i = start + 1; i < ffs.length && res.length < n && i - start <= 10; i++) {
      const t = ffs[i].querySelector(AUTO);
      if (t) res.push(t);
    }
    return res;
  };

  const nat = after('passportNo', 1)[0];
  out.nationality = bind(nat, 'tdac-nationality');
  const dob = after('passportNo', 4).slice(1);          // 国籍之后的 yyyy/mm/dd
  out.dobYear  = bind(dob[0], 'tdac-dobYear');
  out.dobMonth = bind(dob[1], 'tdac-dobMonth');
  out.dobDay   = bind(dob[2], 'tdac-dobDay');
  const res2 = after('visaNo', 2);
  out.countryOfResidence = bind(res2[0], 'tdac-countryOfResidence');
  out.cityOfResidence    = bind(res2[1], 'tdac-cityOfResidence');
  out.countryOfBoarding  = bind(after('arrDate', 1)[0], 'tdac-countryOfBoarding');
  const acc = after('accTypeId', 3);
  out.province    = bind(acc[0], 'tdac-province');
  out.district    = bind(acc[1], 'tdac-district');
  out.subdistrict = bind(acc[2], 'tdac-subdistrict');

  // 3) 供排查用的近邻标签文本（页面标签不是 mat-label，取同级文本）
  const fieldOf = (el) => el ? el.closest('mat-form-field') : null;
  const labelOf = (el) => {
    const ff = fieldOf(el);
    if (!ff) return '';
    let n = ff.previousElementSibling, t = '';
    for (let d = 0; d < 3 && n && !t; d++) { t = (n.innerText || '').replace(/\s+/g, ' ').trim(); n = n.previousElementSibling; }
    return t.slice(0, 60);
  };
  window.__tdacInventory = () => FFS().map((ff, i) => {
    const c = ff.querySelector('input:not([type=hidden]), mat-select, textarea');
    return {
      i,
      fc: c ? (c.getAttribute('formcontrolname') || '') : '',
      role: c ? (c.getAttribute('data-tdac') || '') : '',
      id: c ? (c.id || '') : '',
      ph: c ? (c.getAttribute('placeholder') || '') : '',
      tag: c ? c.tagName.toLowerCase() : '',
      disabled: c ? (c.hasAttribute('disabled') || c.classList.contains('mat-mdc-select-disabled')) : null,
      label: labelOf(c)
    };
  });
  return out;
}
"""


ROLES_STEP1 = [
    ("familyName", "姓", "text"),
    ("firstName", "名", "text"),
    ("middleName", "中间名", "text"),
    ("passportNo", "护照号", "text"),
    ("nationality", "国籍", "auto"),
    ("dobYear", "出生-年", "auto"),
    ("dobMonth", "出生-月", "auto"),
    ("dobDay", "出生-日", "auto"),
    ("occupation", "职业", "text"),
    ("gender", "性别", "radio"),
    ("visaNo", "签证号", "text"),
    ("countryOfResidence", "居住国家", "auto"),
    ("cityOfResidence", "居住城市", "auto"),
    ("phoneCode", "电话区号", "text"),
    ("phoneNo", "手机号", "text"),
]

ROLES_STEP2 = [
    ("arrDate", "入境日期", "date"),
    ("countryOfBoarding", "出发国家", "auto"),
    ("purposeOfTravel", "旅行目的", "select"),
    ("arrivalMode", "入境交通方式", "radio"),
    ("arrivalFlightType", "入境航班类型", "select"),
    ("arrivalFlightNo", "入境航班号", "text"),
    ("departureDate", "离境日期", "date"),
    ("departureMode", "出境交通方式", "radio"),
    ("departureFlightType", "出境航班类型", "select"),
    ("departureFlightNo", "出境航班号", "text"),
    ("accommodationType", "住宿类型", "select"),
    ("province", "府", "auto"),
    ("district", "区", "auto"),
    ("subdistrict", "分区", "auto"),
    ("accPostalCode", "邮政编码", "text"),
    ("accAddress", "详细地址", "text"),
]


# --------------------------------------------------------------------------- #
# 填写动作
# --------------------------------------------------------------------------- #
# 国家/地区名称 -> 官网选项里的 ISO3 代码。
# 官网选项形如「CHN : PEOPLES REPUBLIC OF CHINA」，仅凭 "CHINA" 无法区分
# HKG / MAC（它们同样含 CHINA），因此按代码精确匹配。
COUNTRY_ALIASES = {
    "CHINA": "CHN",
    "THAILAND": "THA",
    "JAPAN": "JPN",
    "SINGAPORE": "SGP",
    "MALAYSIA": "MYS",
    "SOUTH KOREA": "KOR",
    "KOREA": "KOR",
    "VIETNAM": "VNM",
    "LAOS": "LAO",
    "MYANMAR": "MMR",
    "CAMBODIA": "KHM",
    "INDONESIA": "IDN",
    "PHILIPPINES": "PHL",
    "INDIA": "IND",
    "HONG KONG": "HKG",
    "MACAO": "MAC",
    "TAIWAN": "TWN",
    "UNITED STATES": "USA",
    "USA": "USA",
    "UNITED KINGDOM": "GBR",
    "AUSTRALIA": "AUS",
    "CANADA": "CAN",
}


def _opt_score(opt: str, target: str) -> int:
    """给候选选项打分。官网选项常见形式为「CHN : CHINESE」这种「代码 : 名称」。

    必须优先精确匹配代码或名称，绝不能用宽松子串——否则 CHINA 会误配到
    「HKG : HONGKONG SAR, CHINA」这类同样含 CHINA 的选项。
    """
    import re as _re
    o = opt.strip().upper()
    t = target.strip().upper()
    if not t:
        return 0
    alias = COUNTRY_ALIASES.get(t, "")
    tokens = [t] + ([alias] if alias else [])
    best = 0
    for tk in tokens:
        if o == tk:
            best = max(best, 100)
            continue
        parts = [p.strip() for p in _re.split(r"[:：\-–/|]", o) if p.strip()]
        if tk in parts:
            best = max(best, 95)
            continue
        if o.startswith(tk):
            best = max(best, 80)
            continue
        if any(p.startswith(tk) for p in parts):
            best = max(best, 75)
            continue
        if len(tk) >= 4 and any(p.endswith(tk) for p in parts):
            best = max(best, 70)
    return best


def pick_from_panel(page, target: str, timeout: int = 15000, rep: Optional["Rep"] = None) -> bool:
    """在已展开的下拉面板里选中与 target 最匹配的项（精确优先，绝不宽松误配）。"""
    deadline = time.time() + timeout / 1000
    seen: List[str] = []
    while time.time() < deadline:
        opts = page.eval_on_selector_all(
            "mat-option, .mat-mdc-option",
            "els => els.filter(e => e.offsetParent !== null).map(e => (e.innerText||'').replace(/\\s+/g,' ').trim())",
        )
        if opts:
            seen = opts
            scored = sorted(
                ((_opt_score(o, target), i, o) for i, o in enumerate(opts)),
                key=lambda x: (-x[0], x[1]),
            )
            if scored and scored[0][0] > 0:
                best = scored[0][2]
                if rep is not None:
                    top = [f"{o}({s})" for s, i, o in scored[:3]]
                    rep.info(f"  候选打分：{top}  -> 选中「{best}」")
                page.locator("mat-option, .mat-mdc-option").filter(has_text=best).first.click(timeout=5000)
                return True
        time.sleep(0.3)
    if rep is not None:
        rep.info(f"  面板候选（前 8 项）：{seen[:8] if seen else '（未出现任何选项）'}")
    return False


def fill_text(page, role: str, value: str) -> bool:
    loc = page.locator(f'[data-tdac="tdac-{role}"]')
    if loc.count() == 0:
        return False
    loc.first.wait_for(state="visible", timeout=15000)
    loc.first.click()
    loc.first.fill("")
    if value:
        loc.first.type(value, delay=25)
    loc.first.press("Tab")
    return True


def fill_date(page, role: str, value: str) -> bool:
    loc = page.locator(f'[data-tdac="tdac-{role}"]')
    if loc.count() == 0:
        return False
    loc.first.wait_for(state="visible", timeout=15000)
    loc.first.click()
    loc.first.fill("")
    loc.first.type(date_keystrokes(value), delay=40)
    loc.first.press("Tab")
    return True


def fill_autocomplete(page, role: str, value: str, rep: Rep) -> bool:
    loc = page.locator(f'[data-tdac="tdac-{role}"]')
    if loc.count() == 0:
        return False
    el = loc.first
    el.wait_for(state="visible", timeout=20000)
    for _ in range(40):
        if not el.is_disabled():
            break
        time.sleep(0.25)
    el.click()
    el.fill("")
    el.type(value, delay=35)
    # 部分自动完成框（如出生日期 yyyy/mm/dd）不会弹出候选；给 7 秒探测窗口即可
    ok = pick_from_panel(page, value, timeout=7000, rep=rep)
    if not ok:
        el.press("Escape")
        rep.info(f"  {role}：未弹出候选面板，保留直接输入值「{value}」")
    return ok


def fill_select(page, role: str, value: str, rep: Rep) -> bool:
    loc = page.locator(f'[data-tdac="tdac-{role}"]')
    if loc.count() == 0:
        return False
    el = loc.first
    el.wait_for(state="visible", timeout=20000)
    for _ in range(40):
        cls = el.get_attribute("class") or ""
        if "mat-mdc-select-disabled" not in cls:
            break
        time.sleep(0.25)
    el.click()
    ok = pick_from_panel(page, value, rep=rep)
    if not ok:
        page.keyboard.press("Escape")
        rep.warn(f"{role}：下拉未出现匹配项「{value}」")
    return ok


def form_status(page) -> Dict[str, Any]:
    """读出 Angular 表单的无效控件清单与未赋值的隐藏 key，用于定位校验卡点。"""
    try:
        return page.evaluate("""() => {
          const inv = Array.from(document.querySelectorAll('.ng-invalid[formcontrolname]')).map(e => ({
            fc: e.getAttribute('formcontrolname'),
            role: e.getAttribute('data-tdac') || '',
            tag: e.tagName.toLowerCase()
          }));
          const emptyKeys = Array.from(document.querySelectorAll('input[type=hidden][formcontrolname="key"]'))
            .filter(k => !k.value).length;
          const totalKeys = document.querySelectorAll('input[type=hidden][formcontrolname="key"]').length;
          return { invalid: inv, emptyHiddenKeys: emptyKeys, totalHiddenKeys: totalKeys };
        }""")
    except Exception:
        return {"invalid": [], "emptyHiddenKeys": -1, "totalHiddenKeys": -1}


def current_step_invalid(status: Dict[str, Any], selected_step: int) -> List[Dict[str, Any]]:
    """只把当前步骤可见的无效控件作为门禁，忽略未来步骤的共享 formGroup 状态。"""
    roles = ROLES_STEP1 if selected_step == 1 else ROLES_STEP2 if selected_step == 2 else []
    allowed = {f"tdac-{role}" for role, _label, _kind in roles}
    return [item for item in status.get("invalid", [])
            if item.get("step") == selected_step or (item.get("role") in allowed and item.get("visible", True))]


def stepper_state(page) -> List[Dict[str, Any]]:
    try:
        return page.evaluate("""() => Array.from(document.querySelectorAll('mat-step-header')).map(h => ({
          text: (h.innerText||'').replace(/\\s+/g,' ').trim(),
          selected: h.getAttribute('aria-selected') === 'true',
          disabled: h.getAttribute('aria-disabled') === 'true'
        }))""")
    except Exception:
        return []


def fill_radio(page, role: str, value: str, rep: Rep) -> bool:
    grp = page.locator(f'[data-tdac="tdac-{role}"]')
    if grp.count() == 0:
        return False
    btns = grp.first.locator("mat-radio-button")
    n = btns.count()
    if n == 0:
        return False
    v = value.strip().upper()
    # 性别是文字单选；此控件的可见文本是官网唯一稳定的值。
    if role == "gender":
        for i in range(n):
            if (btns.nth(i).inner_text() or "").strip().upper() == v:
                btns.nth(i).click()
                return True
    # 交通方式使用官网 radio input 的机器值；不读取图标或可见文本。
    if role in ("arrivalMode", "departureMode") and v in ("AIR", "LAND", "SEA"):
        # Angular Material 将业务值放在宿主 mat-radio-button 上；内部 input
        # 的 value 可能是官网生成的 opaque token，不能拿来做回读依据。
        host = grp.first.locator(f'mat-radio-button[value="{v}"]')
        if host.count() == 1:
            host.evaluate("e => { e.click(); return true; }")
            return True
        host = grp.first.locator(f'mat-radio-button[ng-reflect-value="{v}"]')
        if host.count() == 1:
            host.evaluate("e => { e.click(); return true; }")
            return True
        choice = grp.first.locator(f'input[value="{v}"]')
        if choice.count() == 1:
            # Playwright.check() 在该官网的隐藏 Material radio 上会把包含
            # NBSP 的可见文本带入异常消息，Windows GBK 输出因此可能再次报错。
            # 直接在浏览器上下文触发原生 click，仍会经过 Angular 的事件链，
            # 但不会把控件说明回传到 Python 编码管道。
            return bool(choice.evaluate("e => { e.click(); return !!e.checked; }"))
    # 图标单选项常包含 NBSP；只从浏览器内返回 ASCII 布尔值，避免 Windows GBK 管道编码。
    icon_map = {
        "AIR": ["flight", "airplane", "plane"],
        "SEA": ["directions_boat", "boat", "ship", "sailing"],
        "LAND": ["directions_car", "car", "bus", "train", "directions_bus"],
    }
    hints = icon_map.get(v, [])
    for i in range(n):
        matched = btns.nth(i).evaluate("""(e, data) => {
          const s = [e.getAttribute('aria-label') || '', e.getAttribute('value') || '',
            e.querySelector('mat-icon')?.textContent || '', e.querySelector('input')?.value || '']
            .join(' ').toLowerCase();
          return s === data.value || data.hints.some(h => s.includes(h));
        }""", {"value": v.lower(), "hints": hints})
        if matched:
            btns.nth(i).click()
            return True
    rep.warn(f"{role}：未能确定「{value}」对应哪一项（该组共 {n} 项）")
    return False


def read_back(page, role: str) -> str:
    loc = page.locator(f'[data-tdac="tdac-{role}"]')
    if loc.count() == 0:
        return "<找不到控件>"
    el = loc.first
    tag = el.evaluate("e => e.tagName.toLowerCase()")
    if tag == "mat-radio-group":
        if role in ("arrivalMode", "departureMode"):
            return el.evaluate(
                "e => { const b = Array.from(e.querySelectorAll('mat-radio-button')).find(x => "
                "x.getAttribute('aria-checked') === 'true' || x.querySelector('input:checked') || "
                "x.classList.contains('mat-mdc-radio-checked') || "
                "x.querySelector('.mdc-radio--checked')) || null; "
                "return b ? (b.getAttribute('value') || b.getAttribute('ng-reflect-value') || "
                "b.getAttribute('aria-label') || (b.innerText || '').trim()) : ''; }"
            ).strip()
        return el.evaluate(
            "e => { const c = e.querySelector('mat-radio-button.mdc-radio--checked, mat-radio-button input:checked');"
            " const all = Array.from(e.querySelectorAll('mat-radio-button'));"
            " const sel = all.find(b => b.querySelector('input:checked') || b.classList.contains('mdc-radio--checked'));"
            " return sel ? (sel.innerText||sel.getAttribute('aria-label')||'[icon]').trim() : ''; }"
        )
    if tag == "mat-select":
        return el.inner_text().strip()
    try:
        return el.input_value().strip()
    except Exception:
        return el.inner_text().strip()


def hidden_key_of(page, role: str):
    """自动完成框真正的表单值在隐藏的 formcontrolname="key" 里；为空说明没真正选中。"""
    try:
        return page.evaluate("""(role) => {
          const el = document.querySelector('[data-tdac="tdac-' + role + '"]');
          if (!el) return null;
          const host = el.closest('.mat-mdc-form-field-infix') || el.parentElement;
          const k = host ? host.querySelector('input[type=hidden][formcontrolname="key"]') : null;
          return k ? (k.value || '') : null;
        }""", role)
    except Exception:
        return None


def verify_field(page, role: str, label: str, expected: str, rep: Rep,
                 mask_role: bool = False) -> bool:
    actual = read_back(page, role)
    exp = expected.strip()
    show_exp = mask(exp) if mask_role else exp
    show_act = mask(actual) if mask_role else actual
    if not exp:
        rep.ok(f"{label}：按配置留空（页面值「{show_act}」）")
        return True
    if actual.upper() == exp.upper() or exp.upper() in actual.upper():
        rep.ok(f"{label}：{show_act}")
        return True
    rep.fail(f"{label}：期望「{show_exp}」，页面显示「{show_act}」")
    return False


# --------------------------------------------------------------------------- #
# 值映射：把配置里的值转成官网下拉里的实际文案
# --------------------------------------------------------------------------- #
def resolve(key: str, cfg: Dict[str, Any]) -> str:
    p, t = cfg["profile"], cfg["trip"]
    table = {
        "familyName": p.get("familyName", ""),
        "firstName": p.get("firstName", ""),
        "middleName": p.get("middleName", ""),
        "passportNo": p.get("passportNo", ""),
        "nationality": p.get("nationality", ""),
        "dobYear": p.get("dateOfBirth", "/").split("/")[0],
        "dobMonth": p.get("dateOfBirth", "/").split("/")[1],
        "dobDay": p.get("dateOfBirth", "/").split("/")[2],
        "occupation": p.get("occupation", ""),
        "gender": p.get("gender", ""),
        "visaNo": p.get("visaNo", ""),
        "countryOfResidence": p.get("countryOfResidence", ""),
        "cityOfResidence": p.get("cityOfResidence", ""),
        "phoneCode": p.get("phoneCode", ""),
        "phoneNo": p.get("phoneNo", ""),
        "arrDate": t.get("arrivalDate", ""),
        "countryOfBoarding": t.get("countryOfBoarding", ""),
        "purposeOfTravel": t.get("purposeOfTravel", ""),
        "arrivalMode": t.get("arrivalMode", ""),
        "arrivalFlightType": t.get("arrivalFlightType", ""),
        "arrivalFlightNo": t.get("arrivalFlightNo", ""),
        "departureDate": t.get("departureDate", ""),
        "departureMode": t.get("departureMode", ""),
        "departureFlightType": t.get("departureFlightType", ""),
        "departureFlightNo": t.get("departureFlightNo", ""),
        "accommodationType": t.get("accommodationType", ""),
        "province": t.get("accommodationProvince", ""),
        "district": t.get("accommodationDistrict", ""),
        "subdistrict": t.get("accommodationSubdistrict", ""),
        "accPostalCode": t.get("accommodationPostalCode", ""),
        "accAddress": t.get("accommodationAddress", ""),
    }
    return table.get(key, "")


MASKED = {"passportNo", "phoneNo"}


def fill_and_verify(page, cfg: Dict[str, Any], roles, rep: Rep, step_name: str) -> bool:
    rep.title(step_name)
    # B1：GUI 安全检查点（CLI 下 _RUN_CONTROL 为 None，等价于无操作）。
    if not _tick(f"{step_name}：准备填写"):
        rep.warn("已按用户要求暂停自动填写，转人工接管。")
        return False
    all_ok = True
    for key, label, kind in roles:
        if not _tick(f"{step_name}：{label}"):
            rep.warn("已按用户要求暂停自动填写，转人工接管。")
            return False
        value = resolve(key, cfg)
        try:
            if kind == "auto":
                fill_autocomplete(page, key, value, rep) if value else None
            elif kind == "select":
                fill_select(page, key, value, rep) if value else None
            elif kind == "radio":
                fill_radio(page, key, value, rep) if value else None
            elif kind == "date":
                fill_date(page, key, value)
            else:
                fill_text(page, key, value)
            time.sleep(0.4)
            # A8：护照号 / 手机号以及航班号 / 邮编 / 地址等，日志一律不回显完整值。
            if not verify_field(page, key, label, value, rep,
                                mask_role=(key in MASKED or key in NO_FULL_LOG_FIELDS)):
                all_ok = False
            if kind == "auto" and value:
                hv = hidden_key_of(page, key)
                if hv is not None and hv == "":
                    rep.info(f"  └ {label}：隐藏值暂为空（官网此控件不暴露 formcontrolname，"
                             f"最终以表单校验与步骤推进为准）")
        except Exception as e:
            rep.fail(f"{label}：填写异常 {type(e).__name__}: {str(e)[:120]}")
            all_ok = False
    return all_ok


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def wait_turnstile(page, rep: Rep, timeout: int = 90000) -> bool:
    rep.info("等待 Cloudflare 人机验证自动通过…（若长时间无反应，请在浏览器窗口内手动勾选）")
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        try:
            v = page.eval_on_selector(
                'input[name="cf-turnstile-response"]', "e => e.value || ''"
            )
            if v and len(v) > 20:
                rep.ok(f"人机验证已通过（token 长度 {len(v)}）")
                return True
        except Exception:
            pass
        time.sleep(1)
    rep.fail("人机验证超时未通过")
    return False


def open_form(page, rep: Rep) -> bool:
    page.goto(TDAC_URL, wait_until="domcontentloaded", timeout=90000)
    if not wait_turnstile(page, rep):
        return False

    # 等按钮真正可用（Turnstile 通过后 Angular 才解除 disabled）
    btn = page.locator("button", has_text="Arrival Card").first
    rep.info("等待 Arrival Card 按钮变为可用…")
    enabled = False
    for _ in range(60):
        try:
            if btn.is_visible() and btn.is_enabled():
                enabled = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not enabled:
        rep.warn("Arrival Card 按钮仍不可用，尝试强制点击")

    rep.info("单次点击 Arrival Card 进入表单")
    try:
        btn.click(timeout=15000)
    except Exception as e:
        rep.fail(f"Arrival Card 点击失败：{type(e).__name__}: {str(e)[:100]}")
        return False
    for _ in range(30):
        current_url = page.evaluate("() => location.href")
        if is_form_url(current_url):
            rep.ok(f"已进入表单：{page.url.split('#')[-1]}")
            return True
        time.sleep(0.5)

    # 诊断信息，便于后续局部修复选择器
    try:
        diag = page.evaluate("""() => ({
          url: location.href,
          buttons: Array.from(document.querySelectorAll('button')).map(b => ({
            text: (b.innerText||'').replace(/\\s+/g,' ').trim().slice(0,40), disabled: b.disabled })),
          tsLen: (document.querySelector('input[name="cf-turnstile-response"]')||{}).value?.length || 0
        })""")
        rep.info(f"诊断：{diag}")
    except Exception:
        pass
    rep.fail(f"未能进入表单页，当前 URL：{page.url}")
    return False


def run_delete_data(args) -> int:
    """A2：删除受管用户数据。只删除数据根目录下由本程序管理的目录。"""
    root = tdac_paths.managed_root()
    paths = tdac_paths.managed_paths()
    print("数据根目录：", root)
    if not paths:
        print("没有需要删除的受管数据。")
        return 0
    print("将删除以下受管数据（仅限本程序管理的目录）：")
    for p in paths:
        print("  -", p)
    if not args.yes:
        try:
            ans = input("确认删除以上全部数据？输入 DELETE 继续：").strip()
        except EOFError:
            ans = ""
        if ans != "DELETE":
            print("已取消，未删除任何内容。")
            return 1
    try:
        removed = tdac_paths.delete_managed_data(confirmed=True)
    except PermissionError as e:
        print("拒绝删除：", e)
        return 1
    except RuntimeError as e:
        # 删除失败必须上报，绝不静默忽略。
        print("删除未全部完成：")
        print(str(e))
        return 2
    print("已删除：", "；".join(removed) or "（无）")
    return 0


def _ask_yes(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _pause_if_interactive(rep: Rep) -> None:
    """仅在有交互终端时等待回车；非交互环境直接返回。"""
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\n按回车结束（浏览器保持打开）…")
    except Exception:
        rep.info("当前环境无交互输入，程序结束；浏览器仍保持打开。")


class _SyntheticPage:
    """dry-run 用的最小页面替身：只提供 locator("body").inner_text()。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def locator(self, selector: str, **kwargs):
        text = self._text

        class _Body:
            def inner_text(self):
                return text
        return _Body()


def run_offline(cfg: Dict[str, Any], rep: Rep, mode: str = "dry-run") -> int:
    """A5：离线检查 —— 不启动浏览器、不触网，输出只写临时沙箱。

    `--smoke` 与 `--dry-run` 共用本函数：两者都必须使用完全合成资料，
    绝不允许访问真实泰国入境卡官网，也不会触发任何提交。
    """
    import tempfile
    label = "SMOKE" if mode == "smoke" else "DRY-RUN"
    rep.title(f"{label} 离线检查（不启动浏览器、不触网）")
    if not cfg.get("synthetic"):
        rep.fail(f"{mode} 只能使用合成资料")
        return rep.close()
    missing = check_config(cfg, rep)
    (rep.fail if missing else rep.ok)("配置完整性：" + ("、".join(missing) if missing else "通过"))
    dyn = cfg.get("dynamic", {})
    for key in ("yellowFeverVaccination", "symptoms"):
        answer = health_answer(dyn, key)
        if answer is None:
            rep.fail(f"健康配置缺值（会触发停止）：{key}")
        else:
            rep.ok(f"健康配置 {key}={answer}")
    from tdac_synth import synthetic_preview_text
    actual = extract_preview_fields(_SyntheticPage(synthetic_preview_text(cfg["trip"])))
    for key in ("arrivalDate", "departureDate", "arrivalFlightNo", "departureFlightNo",
                "accommodationPostalCode"):
        exp = str(cfg["trip"].get(key, ""))
        got = str(actual.get(key, ""))
        if exp and got.strip().upper() != exp.strip().upper():
            rep.fail(f"预览字段映射不一致：{key} 期望 {safe_value(key, exp)} 实际 {safe_value(key, got)}")
        else:
            rep.ok(f"预览字段映射：{key} OK")
    rows = required_fields_rows()
    if any(row.get("填写方式") == "本人手动" for row in rows):
        rep.ok(f"必填项清单完整（{len(rows)} 行，条款为本人手动）")
    else:
        rep.fail("必填项清单缺少『本人手动』条款行")
    # B5：收件邮箱格式与合成值离线校验（不联网）。
    from tdac_validate import validate_email
    email = str((cfg.get("profile") or {}).get("email", ""))
    ok_email, msg_email = validate_email(email)
    (rep.ok if ok_email else rep.fail)(f"收件邮箱格式校验：{msg_email}")
    # B4：脱敏诊断包可离线生成并自检通过。
    try:
        from tdac_diag import render_diagnostic
        render_diagnostic()
        rep.ok("脱敏诊断包离线生成与自检：通过")
    except Exception as e:
        rep.fail(f"脱敏诊断包自检失败：{type(e).__name__}: {str(e)[:120]}")
    # B7：会话/端口说明可离线输出。
    try:
        from tdac_browser import SESSION_NOTES
        rep.ok("会话与调试端口说明：可用（" + str(len(SESSION_NOTES.splitlines())) + " 行）")
    except Exception as e:
        rep.fail(f"会话说明缺失：{type(e).__name__}")
    with tempfile.TemporaryDirectory(prefix="tdac-dryrun-") as sandbox:
        csv_path = write_required_fields_csv(output_dir=sandbox, stamp="dryrun")
        if os.path.exists(csv_path) and os.path.dirname(os.path.abspath(csv_path)) == os.path.abspath(sandbox):
            rep.ok("沙箱输出 CSV 成功（未写入用户数据目录）")
        else:
            rep.fail("沙箱输出异常")
    rep.info(f"{mode} 结束：仅验证本地逻辑，未访问官网、未使用真实资料。")
    return rep.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="泰国电子入境卡（TDAC）本地半自动填写工具")
    ap.add_argument("--check", action="store_true", help="仅做环境与配置检查")
    ap.add_argument("--inspect", action="store_true", help="导出官网表单字段清单（排查用）")
    ap.add_argument("--smoke", action="store_true",
                    help="完全合成资料的离线检查：不启动浏览器、不触网、不读取真实配置")
    ap.add_argument("--dry-run", action="store_true", help="离线检查：不启动浏览器、不触网")
    ap.add_argument("--export-pdf", action="store_true",
                    help="预览校验通过后导出预览 PDF（默认不导出；内容含敏感资料）")
    ap.add_argument("--pdf-path", default="", help="预览 PDF 的保存路径（默认：用户数据目录 output/）")
    ap.add_argument("--delete-data", action="store_true", help="删除本程序管理的用户数据")
    ap.add_argument("--yes", action="store_true", help="与 --delete-data 搭配，跳过交互确认")
    ap.add_argument("--keep-open", action="store_true", help="结束后保留浏览器窗口")
    ap.add_argument("--close-browser", action="store_true",
                    help="结束后关闭本程序启动的浏览器实例（不会影响你日常用的 Chrome）")
    ap.add_argument("--gui", action="store_true", help="启动简体中文图形界面（B1）")
    ap.add_argument("--session-info", action="store_true",
                    help="只读打印浏览器会话与调试端口说明、残留检测结果（B7）")
    ap.add_argument("--export-diagnostic", nargs="?", const="", default=None,
                    help="导出脱敏诊断包；可附带保存路径（B4）")
    ap.add_argument("--check-update", action="store_true",
                    help="经你同意后检查（可配置的）版本来源，仅提示不下载（B3）")
    args = ap.parse_args()

    if args.delete_data:
        return run_delete_data(args)

    if args.gui:
        from tdac_gui import main as gui_main
        return gui_main()

    if args.session_info:
        print(tdac_browser.SESSION_NOTES)
        print()
        print(tdac_browser.describe_leftovers(tdac_browser.detect_leftovers(profile_dir())))
        return 0

    # A2：首次运行按模板在用户数据目录生成配置（不覆盖已有文件）。
    tdac_paths.ensure_user_config()

    if args.export_diagnostic is not None:
        from tdac_diag import collect_diagnostic, config_presence_reader, export_diagnostic
        data = collect_diagnostic(config_presence=config_presence_reader(_read_json))
        path = export_diagnostic(dest_path=(args.export_diagnostic or None), data=data)
        print("诊断包已导出：", path)
        print("（仅包含白名单中的非敏感信息，不含任何真实字段值；请在预览后自行决定是否发送。）")
        return 0

    if args.check_update:
        from tdac_update import check_for_update, describe
        print(describe(check_for_update(consent=True)))
        return 0

    rep = Rep()
    rep._w("泰国电子入境卡（TDAC）本地半自动填写工具")
    rep._w(f"运行时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")

    # ---- 配置 ----
    # A5：dry-run 与 smoke 一样必须使用完全合成资料，绝不读取真实配置。
    synthetic_mode = args.smoke or args.dry_run
    rep.title("0. 配置检查")
    try:
        cfg = load_config(smoke=synthetic_mode)
    except Exception as e:
        rep.fail(f"读取配置失败：{e}")
        return rep.close()

    # 先合并敏感信息，再做完整性检查（否则护照号/手机号会被误判为缺失）
    secret_err = ""
    group_path = os.path.join(config_dir(), "group.json")
    if os.path.exists(group_path) and not synthetic_mode:
        try:
            from tdac_group import load_group_config, validate_group
            from tdac_sec import load_group_secrets
            group = load_group_config(config_dir(), load_group_secrets)
            group_errors = validate_group(group)
            if group_errors:
                secret_err = "；".join(group_errors)
            else:
                cfg["group"] = group
                cfg["profile"] = group.travelers[0].profile()
                cfg["profile"]["email"] = group.receiverEmail or cfg["profile"].get("email", "")
                rep.ok(f"已读取加密团体资料（{len(group.travelers)} 名旅客；当前仅预检/首位旅客流程）")
        except Exception as e:
            secret_err = str(e)[:160]
            rep.warn(f"团体敏感信息读取失败：{secret_err}")
    elif not synthetic_mode:
        try:
            from tdac_sec import load_secrets
            sec = load_secrets()
            for k in ("passportNo", "phoneNo"):
                if sec.get(k):
                    cfg["profile"][k] = sec[k]
            rep.ok(f"已读取加密敏感信息（护照号 {mask(cfg['profile'].get('passportNo', ''))}，"
                   f"手机号 {mask(cfg['profile'].get('phoneNo', ''))}）")
        except Exception as e:
            secret_err = str(e)[:160]
            rep.warn(f"敏感信息读取失败：{secret_err}")
    else:
        rep.warn("合成模式（smoke/dry-run）：使用完全构造的假资料，不读取真实配置、不使用真实护照号 / 手机号 / 真实提交")

    missing = check_config(cfg, rep)
    if secret_err:
        missing += ["secrets.group" if os.path.exists(group_path) else "secrets.passportNo", "secrets.phoneNo"]

    if missing:
        rep.fail("缺少必填配置：" + "、".join(missing))
    else:
        rep.ok("配置完整")

    # A7：真实运行时对关键字段（护照号/手机号/邮箱）做二次确认。
    # 合成 / 离线 / 排查模式不需要人工确认。
    #
    # 不再提供 ``--skip-confirm``。落盘指纹只用于提示资料是否变化，绝不能
    # 授权预填；CLI 必须在本次操作中重新人工确认，或接收 GUI 同进程交接的
    # 一次性内存授权。
    if not missing and not (args.smoke or args.inspect or args.dry_run):
        import tdac_gate
        dynamic = cfg.get("dynamic") or {}
        if tdac_gate.has_current_prefill_authorization(cfg["profile"], dynamic, cfg["trip"]):
            rep.ok("已收到图形界面本次操作的一次性确认授权")
        else:
            if not sys.stdin or not sys.stdin.isatty():
                rep.fail("当前不是交互式终端，无法完成本次关键字段与健康声明确认。")
                rep.info("请在图形界面中点击「开始正式预填」，或在交互式终端运行本程序。")
                return rep.close()
            from tdac_privacy import confirm_critical_fields
            cf_ok, unconfirmed = confirm_critical_fields(cfg["profile"], _ask_yes)
            if not cf_ok:
                rep.fail("关键字段未确认：" + "、".join(unconfirmed))
                return rep.close()
            if not tdac_gate.health_confirmed(dynamic):
                rep.fail("健康声明尚未完整确认；请先在图形界面保存健康声明后重试。")
                return rep.close()
            if not _ask_yes("请确认：以上健康声明答案是本次申报所填写的真实情况，是否继续？"):
                rep.fail("健康声明未在本次操作中确认。")
                return rep.close()
            tdac_gate.record_critical_confirmation(cfg["profile"], config_dir())
            tdac_gate.authorize_current_prefill(cfg["profile"], dynamic, cfg["trip"])
            rep.ok("关键字段与健康声明已在本次操作中确认")

    # ---- 窗口 ----
    rep.title("0b. 申报窗口检查")
    in_window, msg = check_window(cfg["trip"])
    (rep.ok if in_window else rep.warn)(msg)

    # A5：离线检查模式 —— smoke / dry-run 一律不启动浏览器、不触网。
    # 这是硬边界：--smoke 不再访问真实官网，未来如需官网交互测试必须另行授权。
    if args.dry_run or args.smoke:
        return run_offline(cfg, rep, mode=("smoke" if args.smoke else "dry-run"))

    if not in_window and not args.inspect:
        rep.info("未进入申报窗口：只执行环境与配置检查，不进行正式申报流程。")
        rep.info("（如需离线演练填写逻辑，可运行： python tdac_fill.py --dry-run）")
        return rep.close()

    # ---- 环境 ----
    browser, port = preflight_env(rep)

    if args.check:
        return rep.close()

    # G1 最终修复：统一正式预填门禁（界面 / 命令行 / 便携版一致）。
    # 不满足“完整 + 格式 + 关键字段本人确认 + 日期窗口 + 健康当次确认”时，
    # 绝不打开官网。--inspect 仅是字段排查，不填写个人资料，故不受此门禁约束。
    if not synthetic_mode and not args.inspect:
        import tdac_gate
        groups = tdac_gate.prefill_blocker_groups(
            cfg["profile"], cfg["trip"], cfg.get("dynamic") or {})
        if groups:
            rep.fail("尚未满足正式预填的全部条件，已阻止打开官网：")
            for name, items in groups:
                rep.info(f"【{name}】")
                for item in items:
                    rep.info("  · " + item)
            rep.info("提示：资料可先保存为草稿稍后补齐；补齐后重试。")
            return rep.close()

    if missing or not browser:
        rep.info("存在阻断问题，未打开浏览器。")
        return rep.close()

    # ---- 浏览器 + CDP ----
    rep.title("2. 启动浏览器并接管")
    proc = launch_browser(browser, port, rep)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        pw_browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = pw_browser.contexts[0] if pw_browser.contexts else pw_browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        rep.ok("Playwright 已通过 CDP 接管浏览器")

        rep.title("3. 打开官网并进入表单")
        if not open_form(page, rep):
            # A4：失败时保留浏览器页面供人工接管；只有用户明确要求才关闭专用会话。
            rep.warn("未能进入表单：已保留浏览器页面供人工接管。")
            if args.close_browser and not args.keep_open:
                try:
                    pw_browser.close()
                except Exception:
                    pass
                try:
                    proc.terminate()
                except Exception:
                    pass
            else:
                rep.info("浏览器保持打开；人工修正后可直接在页面中继续，处理完手动关闭窗口。")
                _pause_if_interactive(rep)
            return rep.close()

        page.evaluate(BIND_JS)
        rep.ok("已绑定控件角色（data-tdac）")

        if args.inspect:
            inv = page.evaluate("() => window.__tdacInventory()")
            rep.title("表单字段清单")
            for row in inv:
                rep.info(f"i={row['i']:<3} tag={row['tag']:<11} fc={row['fc']:<18} "
                         f"role={row['role']:<28} id={row['id']:<18} ph={row['ph'][:24]:<24} "
                         f"disabled={row['disabled']}")
            radio_diag = page.evaluate("""() => Array.from(document.querySelectorAll(
              '[data-tdac="tdac-arrivalMode"], [data-tdac="tdac-departureMode"]'
            )).map(g => ({
              role: g.getAttribute('data-tdac'),
              buttons: Array.from(g.querySelectorAll('mat-radio-button')).map(b => ({
                value: b.getAttribute('value') || '',
                ngValue: b.getAttribute('ng-reflect-value') || '',
                ariaChecked: b.getAttribute('aria-checked') || '',
                ariaLabel: b.getAttribute('aria-label') || '',
                text: (b.innerText || '').replace(/\\s+/g, ' ').trim(),
                inputValue: b.querySelector('input')?.value || ''
              }))
            }))""")
            rep.info("交通方式控件诊断：" + json.dumps(radio_diag, ensure_ascii=True))
            try:
                pw_browser.close()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
            return rep.close()

        rep.title("4. 第一阶段：个人信息")
        ok1 = fill_and_verify(page, cfg, ROLES_STEP1, rep, "填写与回读校验")

        st = form_status(page)
        if st["invalid"]:
            rep.warn("仍有控件未通过校验：" + "、".join(
                f"{i['role'] or i['fc']}({i['tag']})" for i in st["invalid"]))
        else:
            rep.ok("第一阶段控件校验通过")
        rep.info(f"未赋值的隐藏 key：{st['emptyHiddenKeys']}/{st['totalHiddenKeys']}")

        rep.title("5. 进入下一步")
        advanced = False
        current_invalid = current_step_invalid(st, selected_step=1)
        if current_invalid:
            rep.warn("当前步骤仍有控件未通过校验：" + "、".join(
                f"{i['role'] or i['fc']}({i['tag']})" for i in current_invalid))
        if ok1 and not current_invalid:
            if not click_enabled_button(page, "Continue", timeout_ms=20000):
                rep.fail("第 1 步未找到可见且启用的 Continue 按钮")
            else:
                time.sleep(3.5)
                page.evaluate(BIND_JS)
                steps = stepper_state(page)
                for s in steps:
                    rep.info(f"步骤「{s['text']}」 selected={s['selected']} disabled={s['disabled']}")
                sel = [s["text"] for s in steps if s["selected"]]
                if sel and sel[0].strip().startswith("2"):
                    rep.ok(f"已进入第 2 步：{sel[0]}")
                    advanced = True
                else:
                    rep.warn(f"Continue 后仍停留在「{sel[0] if sel else '未知'}」——第 1 步尚未通过官网校验，"
                             f"请在浏览器窗口中按页面提示核对。")
        else:
            rep.warn("第一阶段存在未通过项，暂不点击 Continue（请先在浏览器里核对页面提示）")

        rep.title("6. 第二阶段：旅行与住宿")
        ok2 = False
        if advanced:
            ok2 = fill_and_verify(page, cfg, ROLES_STEP2, rep, "填写与回读校验")
            st2 = form_status(page)
            if st2["invalid"]:
                rep.warn("仍有控件未通过校验：" + "、".join(
                    f"{i['role'] or i['fc']}" for i in st2["invalid"]))
        else:
            rep.warn("未进入第 2 步，本阶段跳过（第 1 步通过后会自动继续）。")

        preview_ok = False
        pdf_path = ""
        template_path = ""
        if ok2:
            try:
                if click_enabled_button(page, "Continue"):
                    preview_ok = advance_health_and_preview(page, cfg, rep)
                else:
                    rep.fail("第 2 步未找到可见且启用的 Continue 按钮")
            except Exception as e:
                rep.fail(f"健康/预览阶段异常：{type(e).__name__}: {str(e)[:100]}")
        else:
            rep.warn("第二阶段未通过，跳过健康与预览。")

        if preview_ok:
            # A3：默认不导出预览 PDF；仅当用户主动选择 --export-pdf 时才导出并提示敏感。
            if args.export_pdf:
                rep.warn("预览 PDF 可能包含完整个人资料，请按敏感文件保管，勿公开分享。")
                try:
                    pdf_path = export_preview_pdf(page, path=(args.pdf_path or None))
                    rep.ok(f"预览页 PDF 已保存：{pdf_path}")
                except Exception as e:
                    rep.fail(f"预览页 PDF 导出失败：{type(e).__name__}: {str(e)[:160]}")
            else:
                rep.info("按默认设置未导出预览 PDF；如需导出请加 --export-pdf（内容含敏感资料）。")
            try:
                template_path = write_required_fields_csv()
                rep.ok(f"同行人必填信息表已保存：{template_path}")
            except Exception as e:
                rep.fail(f"同行人必填信息表生成失败：{type(e).__name__}: {str(e)[:160]}")

        rep.title("8. 结果")
        rep.info(f"第一阶段：{'PASS' if ok1 else 'FAIL'}")
        rep.info(f"第二阶段：{'PASS' if ok2 else 'FAIL（可能因未进入该步或需窗口开放）'}")
        rep.info(f"预览阶段：{'PASS' if preview_ok else 'FAIL（未确认到预览组件）'}")
        rep.info(f"PDF 导出：{'PASS' if pdf_path else ('未导出（默认）' if not args.export_pdf else 'FAIL')}")
        rep.info(f"必填项表格：{'PASS' if template_path else 'FAIL'}")
        rep.warn("最终提交按钮必须由你本人点击——程序不会提交、不会同意法律条款。")

        # A4：仅当流程完整走完、或用户明确要求关闭时才断开；否则保留页面供人工接管。
        session_complete = ok1 and ok2 and preview_ok
        if session_complete or args.close_browser:
            try:
                pw_browser.close()
            except Exception:
                pass
        else:
            rep.info("自动流程未完成：已保留浏览器页面供人工接管；确认无误后请手动关闭窗口。")
            _pause_if_interactive(rep)

    # 只结束本程序启动的那个浏览器实例，绝不动你日常使用的 Chrome
    if args.close_browser and not args.keep_open:
        try:
            proc.terminate()
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.25)
        except Exception:
            pass

    if args.keep_open:
        try:
            input("\n按回车结束（浏览器保持打开）…")
        except EOFError:
            rep.warn("当前终端没有交互输入，程序结束；浏览器仍保持打开。")
    return rep.close()


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)
