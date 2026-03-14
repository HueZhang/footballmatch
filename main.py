import collections
import csv
import io
import json
import os
import smtplib
from datetime import datetime, timezone, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
import requests
from bs4 import BeautifulSoup


# sofascore或其他，okoo只有可投注的比赛，先用okoo
DATA_URL = "https://www.okooo.com/jingcai/"

# --- GitHub action 获取配置---
APP_ID = os.environ.get("APP_ID")
APP_SECRET = os.environ.get("APP_SECRET")
TEMPLATE_ID = os.environ.get("TEMPLATE_ID")
F1_TEMPLATE_ID = "AXjbhHbIq3ycr2YZOCPbTcR71LlKA0N7KKhZVVNqoyo"
OPEN_ID = os.environ.get("OPEN_ID")
MYTEAM = "曼联"
F1_JSON = "f1_2026_schedule.json"
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
#
SENDER_RECV_EMAIL = os.environ.get("SENDER_RECV_EMAIL")
# 不是邮箱登录密码，是SMTP授权码
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")


# 获取网页
def get_html():
    """获取网页源码"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
    }
    try:
        # 如果是本地HTML文件测试，可以注释掉 requests，读取本地文件
        resp = requests.get(DATA_URL, headers=headers, timeout=10)
        resp.encoding = "gb2312"  # 澳客网通常是gb2312或gbk，如果是乱码请改为 'utf-8'
        return resp.text
    except Exception as e:
        print(f"请求网页出错: {e}")
        return None


def safe_text(parent_element, tag_name, class_name=None, default="-"):
    """
    辅助函数：安全获取标签文本
    如果父元素不存在，或者找不到子元素，返回默认值，防止报错
    """
    if not parent_element:
        return default

    if class_name:
        element = parent_element.find(tag_name, class_=class_name)
    else:
        element = parent_element.find(tag_name)

    if element:
        return element.text.strip()
    return default


def parse_data(html_content):
    """核心解析逻辑 (已加强容错)"""
    soup = BeautifulSoup(html_content, "html.parser")

    box = soup.find("div", id="content", class_="box")
    all_cont = box.find_all("div", class_="cont")

    cont = all_cont[1]
    # 1. 找到所有 div class='touzhu'
    touzhu_divs = cont.find_all("div", class_="touzhu")

    match_data_list = []

    for container in touzhu_divs:
        # 2. 找下面所有的 div class='touzhu_1' 且 data-end="0"
        matches = container.find_all("div", class_="touzhu_1", attrs={"data-end": "0"})

        for match in matches:
            try:
                item = {}

                # --- 基本信息 ---
                liansai_div = match.find("div", class_="liansai")
                # 使用 safe_text 替代直接 .text
                item["league_name"] = safe_text(liansai_div, "a", "saiming")

                match_time = (
                    liansai_div.find("div", class_="shijian").get("title", "-")
                    if liansai_div and liansai_div.find("div", class_="shijian")
                    else "-"
                )
                item["time"] = match_time[5:]
                # --- 胜平负信息 (shenpf) ---
                shenpf_div = match.find("div", class_="shenpf")

                # 主队
                zhu_div = shenpf_div.find("div", class_="zhu") if shenpf_div else None
                item["home_name"] = safe_text(zhu_div, "div", "zhum")
                # item['home_odd'] = safe_text(zhu_div, 'div', 'peilv')

                # 客队
                fu_div = shenpf_div.find("div", class_="fu") if shenpf_div else None
                item["away_name"] = safe_text(fu_div, "div", "zhum")
                # item['away_odd'] = safe_text(fu_div, 'div', 'peilv')

                match_data_list.append(item)

            except Exception as e:
                # --- 重点：这里是调试的关键 ---
                match_id = match.get("data-mid", "未知ID")
                print(f"!!! 解析出错 (ID: {match_id}): {e}")
                import traceback

                traceback.print_exc()  # 打印详细报错行数

                continue  # 跳过这一场，继续解析下一场

    return match_data_list


# 过滤用户关注的队伍
def filter_user_matches(user_configs, match_list):
    """
    user_configs: [{"openid": "...", "homeTeam": "曼联,阿根廷"}, ...]
    match_list: 获取到的原始比赛数据列表
    """
    user_results = collections.defaultdict(list)

    # 1. 直接遍历对象数组
    for config in user_configs:
        contact = config.get("openid")
        raw_teams = config.get("homeTeam", "")

        if not contact or not raw_teams:
            continue

        # 2. 解析关注的球队（兼容中英文逗号，去除多余空格）
        # 将 "曼联,阿根廷" 转换为 {"曼联", "阿根廷"}
        teams = {t.strip() for t in raw_teams.replace("，", ",").split(",")}

        # 3. 匹配比赛 (O(N) 筛选)
        for m in match_list:
            # 只要主队或客队在关注名单中，即命中
            if m["home_name"] in teams or m["away_name"] in teams:
                user_results[contact].append(m)

    return dict(user_results)


def filter_my_matches(match_list):
    for m in match_list:
        # 只要主队或客队在关注名单中，即命中
        if m["home_name"] == MYTEAM or m["away_name"] in MYTEAM:
            return m


# 获取accesstoken
def get_access_token():
    """获取 access_token"""
    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={APP_ID}&secret={APP_SECRET}"
    )
    response = requests.get(url)
    result = response.json()
    if "access_token" in result:
        return result["access_token"]
    else:
        print(f"获取 access_token 失败: {result}")
        return None


# 获取所有关注的用户  暂时不需要
def get_all_openids(access_token):
    all_openids = []
    next_openid = ""  # 第一次拉取，从第一个开始

    while True:
        # 调用获取用户列表的接口 [citation:4][citation:6]
        url = (
            "https://api.weixin.qq.com/cgi-bin/user/get"
            f"?access_token={access_token}&next_openid={next_openid}"
        )
        response = requests.get(url)
        result = response.json()

        if "errcode" in result:
            print(f"获取 OpenID 列表失败: {result}")
            return None

        current_batch = result.get("data", {}).get("openid", [])
        all_openids.extend(current_batch)
        print(f"已拉取 {len(current_batch)} 个 OpenID，累计 {len(all_openids)} 个")

        # 判断是否拉取完毕 [citation:4]
        # 如果返回的 next_openid 为空字符串，说明已经拉取完所有用户
        next_openid = result.get("next_openid", "")
        if not next_openid:
            break

    return all_openids


# 发送消息
def send_msg(access_token, my_match):
    body = {
        "touser": OPEN_ID.strip(),
        "template_id": TEMPLATE_ID.strip(),
        "url": "https://weixin.qq.com",  # 可以改成你的赛事详情页
        "data": {
            "leagueName": {"value": my_match["league_name"]},
            "time": {"value": my_match["time"]},
            "homeName": {"value": my_match["home_name"]},
            "awayName": {"value": my_match["away_name"]},
        },
    }
    url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
    print(requests.post(url, json.dumps(body)).text)


# 检查F1赛程，返回今天还未开始的比赛列表
def check_f1_schedule(file_path):
    # 1. 加载 JSON 数据
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 2. 获取当前 UTC 时间和日期字符串
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")

    # 定义北京时间偏移量 (UTC+8)
    beijing_offset = timedelta(hours=8)

    reminders = []

    # 3. 遍历所有分站
    for event in data["schedule"]:
        gp_name = event["gp_name"]

        # 4. 遍历该分站的所有阶段 (Session)
        for session_name, session_time_str in event["sessions"].items():
            # 解析 session_time 为带时区的 datetime 对象
            # replace('Z', '+00:00') 是为了让 fromisoformat 能够识别 UTC 标志
            session_dt = datetime.fromisoformat(session_time_str.replace("Z", "+00:00"))

            # 5. 修改后的判断条件：
            # a. UTC 日期匹配今天 (today_str)
            # b. 且当前 UTC 时间在 session 开始时间之前
            if session_time_str.startswith(today_str) and now_utc < session_dt:
                print(
                    f"检查 {gp_name} - {session_name}: 赛程时间 {session_time_str} (UTC), "
                    f"当前时间 {now_utc.isoformat()} (UTC)"
                )

                # 6. 转换为北京时间 (UTC+8)
                bj_dt = session_dt + beijing_offset
                # 格式化为 HH:MM 形式
                time_part = bj_dt.strftime("%H:%M")

                # 处理名称解析
                cn_name = gp_name.split("(")[1].replace(")", "").strip() if "(" in gp_name else gp_name
                en_name = gp_name.split("(")[0].strip() if "(" in gp_name else gp_name

                reminders.append(
                    {
                        "gp": en_name,
                        "gpCN": cn_name,
                        "session": session_name.upper(),
                        "time": time_part,  # 此时为北京时间
                    }
                )

    return reminders


def send_f1_msg():
    access_token = get_access_token()
    my_match = check_f1_schedule(F1_JSON)
    for match in my_match:
        body = {
            "touser": OPEN_ID.strip(),
            "template_id": F1_TEMPLATE_ID.strip(),
            "url": "https://weixin.qq.com",  # 可以改成你的赛事详情页
            "data": {
                "gpName": {"value": match["gp"]},
                "gpNameCN": {"value": match["gpCN"]},
                "sessionName": {"value": match["session"]},
                "time": {"value": match["time"]},
            },
        }
        url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"
        print(requests.post(url, json.dumps(body)).text)


def convert_to_csv_string(match_data_list):
    """
    将解析出的比赛数据列表转换为 CSV 格式字符串
    字段：联赛名称, 主队, 客队
    """
    if not match_data_list:
        return ""

    # 使用 StringIO 在内存中构建文件
    output = io.StringIO()

    # 定义表头映射 (代码中的 key -> CSV 中的显示名称)
    field_map = {
        "league_name": "联赛名称",
        "home_name": "主队",
        "away_name": "客队",
    }

    # 初始化 CSV 写入器
    # line_terminator='\n' 确保跨平台换行符一致
    writer = csv.DictWriter(output, fieldnames=field_map.keys(), extrasaction="ignore")

    # 写入表头 (自定义中文表头)
    output.write(",".join(field_map.values()) + "\n")

    # 写入数据行
    for item in match_data_list + 1:
        writer.writerow(item)

    # 获取字符串内容
    csv_content = output.getvalue()
    output.close()

    return csv_content


def send_email_csv(csv_content):
    message = MIMEMultipart()
    message["From"] = formataddr(["数据抓取助手", SENDER_RECV_EMAIL])
    today_str = datetime.now().strftime("%Y-%m-%d")
    message["Subject"] = Header(f"【定时日报】{today_str} 比赛数据整理", "utf-8")
    message["To"] = ",".join(SENDER_RECV_EMAIL)

    # 正文
    message.attach(MIMEText("附件为今日解析的比赛列表，请查收。", "plain", "utf-8"))

    # 附件
    attachment = MIMEText(csv_content, "txt", "utf-8")
    attachment.add_header("Content-Disposition", "attachment", filename="matches.txt")
    message.attach(attachment)

    # 发送邮件
    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SENDER_RECV_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_RECV_EMAIL, SENDER_RECV_EMAIL, message.as_string())
        server.quit()
    except Exception as e:
        print(f"发送邮件失败: {e}")


def main():
    # 发送f1赛程提醒
    send_f1_msg()
    # 获取网页
    html = get_html()
    if html:
        data = parse_data(html)
        # 执行筛选
        # final_results = filter_user_matches(user_data, data)
        # 主队信息提醒
        final_results = filter_my_matches(data)
        if final_results:
            print(json.dumps(final_results, ensure_ascii=False, indent=2))
            token = get_access_token()
            send_msg(token, final_results)

        # 转换为 CSV 字符串（如果需要发送邮件或其他用途）
        csv_string = convert_to_csv_string(data)
        send_email_csv(csv_string)
    else:
        print("未获取到网页数据，任务终止。")


if __name__ == "__main__":
    main()
