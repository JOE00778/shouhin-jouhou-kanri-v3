"""停产监控配置"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 容器内通过 STOCK_MONITOR_CSV 环境变量指向挂载路径；
# 否则落回到项目上一级目录下的 CSV（host 直接运行时使用）
_csv_env = os.environ.get("STOCK_MONITOR_CSV")
CSV_SRC = Path(_csv_env) if _csv_env else ROOT.parent / "item_master_bilingual.csv"

COOKIES_DIR = ROOT / "cookies"
STATE_DIR = ROOT / "state"
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"

# 每站 Cookie 文件（Netscape cookies.txt 格式，由浏览器导出）
COOKIE_NETDEOROSHI = COOKIES_DIR / "netdeoroshi.cookies.txt"
COOKIE_SUPERDELIVERY = COOKIES_DIR / "superdelivery.cookies.txt"

# 请求节流（秒）
REQ_INTERVAL_SEC = 1.5
REQ_TIMEOUT_SEC = 15

# 飞书电子表格（周度变化追加至此）
LARK_SHEET_TOKEN = "WhdnsP15phaGBAtw0jvcH7bwn2f"
LARK_SHEET_ID = "6578a4"
LARK_SHEET_URL = "https://ra9v81dggsk.feishu.cn/sheets/WhdnsP15phaGBAtw0jvcH7bwn2f"

# User-Agent 伪装
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
