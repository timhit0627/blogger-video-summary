import os
import json
import requests
from datetime import datetime, timedelta

# ========== 全局环境变量 ==========
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_CREATORS_DOC_ID = os.getenv("FEISHU_CREATORS_DOC_ID")  # 电子表格（sheet）ID
FEISHU_SUMMARY_DOC_ID = os.getenv("FEISHU_SUMMARY_DOC_ID")    # 汇总表（电子表格）ID
AI_API_KEY = os.getenv("AI_API_KEY")

# AI配置
AI_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
AI_MODEL = "glm-4-flash"

# ========== 工具函数 ==========
def get_feishu_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        response = requests.post(
            url,
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=15
        )
        response.raise_for_status()
        token = response.json()["tenant_access_token"]
        print(f"✅ 飞书令牌获取成功，令牌前8位：{token[:8]}...")
        return token
    except Exception as e:
        print(f"❌ 飞书令牌获取失败：{str(e)}")
        return None

# ---------------- 核心修正：读取电子表格（sheet/v2）----------------
def read_creators_from_feishu():
    token = get_feishu_token()
    if not token:
        print("❌ 无有效飞书令牌，无法读取博主名单")
        return []

    # ✅ 改用 sheet/v2 读取电子表格（不是 docx）
    url = f"https://open.feishu.cn/open-apis/sheet/v2/spreadsheets/{FEISHU_CREATORS_DOC_ID}/values/Sheet1"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        res_json = response.json()

        if "data" not in res_json or "valueRange" not in res_json["data"]:
            print(f"❌ 表格返回格式异常：{res_json}")
            return []

        rows = res_json["data"]["valueRange"]["values"]
        print(f"✅ 电子表格读取成功，共 {len(rows)} 行（含表头）")
    except Exception as e:
        print(f"❌ 电子表格读取失败：{str(e)}")
        return []

    creators = []
    if len(rows) < 2:
        print("⚠️ 表格无数据")
        return []

    # 跳过表头
    for row in rows[1:]:
        if len(row) != 4:
            print(f"⚠️ 行格式错误（需4列）跳过：{row}")
            continue
        name, platform, url, enable = row
        enable = str(enable).strip().lower() == "true"
        if platform not in ["douyin", "youtube"]:
            print(f"⚠️ 平台错误跳过：{name}")
            continue
        platform_show = "抖音" if platform == "douyin" else "YouTube"
        creators.append({
            "name": name,
            "platform": platform,
            "platform_show": platform_show,
            "url": url.strip(),
            "enable": enable
        })

    active = [c for c in creators if c["enable"]]
    print(f"✅ 有效博主：{len(active)} 个")
    return active

def save_history(history_data):
    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)
    print(f"✅ 历史记录已保存")

def get_latest_videos(platform, home_url):
    today = datetime.now().strftime("%Y-%m-%d")
    mock_videos = [
        {
            "vid": f"{platform}_{today}_{datetime.now().strftime('%H%M%S')}",
            "title": f"测试视频 {today}",
            "video_url": home_url,
            "content": "测试内容",
            "publish_time": today
        }
    ]
    try:
        with open("history.json", "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        history = {"douyin": [], "youtube": []}
    new_videos = [v for v in mock_videos if v["vid"] not in history[platform]]
    print(f"ℹ️ {platform} 新增 {len(new_videos)} 条")
    return new_videos

def ai_classify_summary(content):
    prompt = f"""
请总结视频内容，严格分两块：
【投资观点】：大盘、板块、仓位、买卖、趋势
【认知/价值观观点】：人生、思维、长期主义、成长、处世
无内容写“无相关观点”，不要多余文字。
内容：{content}
    """
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    try:
        response = requests.post(AI_URL, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        summary = response.json()["choices"][0]["message"]["content"]
        investment = "无相关观点"
        cognition = "无相关观点"
        if "【投资观点】" in summary and "【认知/价值观观点】" in summary:
            investment = summary.split("【投资观点】")[1].split("【认知/价值观观点】")[0].strip("：\n")
            cognition = summary.split("【认知/价值观观点】")[1].strip("：\n")
        return investment, cognition
    except Exception as e:
        print(f"❌ AI总结失败：{e}")
        return "AI总结失败", "AI总结失败"

def append_to_today_table(row_data):
    token = get_feishu_token()
    if not token:
        print("❌ 无令牌无法写入")
        return
    url = f"https://open.feishu.cn/open-apis/sheet/v2/spreadsheets/{FEISHU_SUMMARY_DOC_ID}/values/Sheet1:append"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {
        "valueRange": {"values": [row_data]},
        "insertDataOption": "INSERT_ROWS",
        "valueInputOption": "RAW"
    }
    try:
        response = requests.post(url, json=data, headers=headers, timeout=15)
        response.raise_for_status()
        print(f"✅ 写入汇总表成功")
    except Exception as e:
        print(f"❌ 写入汇总表失败：{e}")

# ========== 主逻辑 ==========
def main():
    try:
        with open("history.json", "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        history = {"douyin": [], "youtube": []}

    active_creators = read_creators_from_feishu()
    if not active_creators:
        print("ℹ️ 无启用博主，退出")
        return

    today_date = datetime.now().strftime("%Y-%m-%d").replace("-0", "-")
    update_time = datetime.now().strftime("%H:%M")
    has_new_video = False

    for creator in active_creators:
        platform = creator["platform"]
        name = creator["name"]
        platform_show = creator["platform_show"]
        home_url = creator["url"]

        videos = get_latest_videos(platform, home_url)
        if not videos:
            continue

        for video in videos:
            has_new_video = True
            vid = video["vid"]
            title = video["title"]
            video_url = video["video_url"]
            content = video["content"]

            inv, cog = ai_classify_summary(content)

            row = [
                update_time,
                name,
                platform_show,
                title,
                video_url,
                inv,
                cog
            ]
            append_to_today_table(row)
            history[platform].append(vid)
            print(f"✅ 处理完成：{name} - {title}")

    if has_new_video:
        save_history(history)
        print(f"✅ 本轮更新完成")
    else:
        print(f"ℹ️ 无新视频")

if __name__ == "__main__":
    main()
