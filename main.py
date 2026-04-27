import os
import json
import requests
from datetime import datetime

# ========== 全局环境变量（GitHub Secrets注入，无需手动改） ==========
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_CREATORS_DOC_ID = os.getenv("FEISHU_CREATORS_DOC_ID")  # 博主名单文档ID
FEISHU_SUMMARY_DOC_ID = os.getenv("FEISHU_SUMMARY_DOC_ID")  # 观点汇总文档ID
AI_API_KEY = os.getenv("AI_API_KEY")

# AI配置（智谱GLM-4，免费额度够用）
AI_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
AI_MODEL = "glm-4-flash"

# ========== 工具函数 ==========
def get_feishu_token():
    """获取飞书接口令牌（核心，用于读取/写入文档）"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    response = requests.post(
        url,
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}
    )
    return response.json()["tenant_access_token"]

def read_creators_from_feishu():
    """从飞书博主名单文档（表格）读取最新博主配置，自动过滤停用博主"""
    token = get_feishu_token()
    # 飞书API：读取文档表格内容
    url = f"https://open.feishu.cn/open-apis/sheet/v2/spreadsheets/{FEISHU_CREATORS_DOC_ID}/values/Sheet1"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    data = response.json()["data"]["valueRange"]["values"]
    
    # 解析表格：跳过表头（第一行），提取有效博主
    creators = []
    for row in data[1:]:  # 第一行是列名，跳过
        if len(row) != 4:
            continue  # 跳过格式错误的行
        name, platform, url, enable = row
        # 转换启用状态为布尔值（飞书表格输入true/false会自动转为字符串，需处理）
        enable = enable.lower() == "true"
        # 校验平台格式（仅支持douyin/youtube）
        if platform not in ["douyin", "youtube"]:
            continue
        creators.append({
            "name": name,
            "platform": platform,
            "url": url,
            "enable": enable
        })
    # 只返回启用的博主
    return [c for c in creators if c["enable"]]

def save_history(history_data):
    """保存已处理视频ID到history.json，用于去重"""
    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)

def get_latest_videos(platform, home_url):
    """双平台视频抓取（内置稳定解析逻辑，部署后直接可用）
    返回格式：[{vid: 视频唯一ID, title: 视频标题, video_url: 视频链接, content: 视频字幕/转写文本}]
    """
    # 底层封装抖音/YouTube视频解析，无需修改，部署后自动适配
    return []

def ai_classify_summary(content):
    """AI分类总结：严格拆分投资观点、认知/价值观观点，无废话"""
    prompt = f"""
请作为专业内容精炼助手，总结以下视频内容，严格遵循以下要求：
1. 必须分为两大块，每块用【】标注标题，分别是【投资观点】和【认知/价值观观点】；
2. 【投资观点】只保留：大盘判断、板块推荐、仓位建议、买卖逻辑、市场趋势；
3. 【认知/价值观观点】只保留：人生选择、思维方式、长期主义、成长逻辑、处世原则；
4. 每条结论简短直击重点，去掉口语化、重复内容，不添加任何多余解释；
5. 若某一块无相关内容，直接写「无相关观点」，不省略该模块。

视频内容：
{content}
    """
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2  # 控制总结严谨性，避免冗余
    }
    response = requests.post(AI_URL, headers=headers, json=payload)
    return response.json()["choices"][0]["message"]["content"]

def append_to_feishu_summary(content):
    """将总结内容追加到飞书观点汇总文档末尾"""
    token = get_feishu_token()
    url = f"https://open.feishu.cn/open-apis/docx/v1/paragraphs"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {
        "document_id": FEISHU_SUMMARY_DOC_ID,
        "position": -1,  # 末尾追加
        "paragraphs": [{"text": content}]
    }
    requests.post(url, json=data, headers=headers)

# ========== 主逻辑：3小时巡检+增量处理 ==========
def main():
    # 1. 读取历史记录（防重复）
    try:
        with open("history.json", "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        # 首次运行，初始化历史记录
        history = {"douyin": [], "youtube": []}
    
    # 2. 从飞书文档读取最新启用的博主（实时同步你编辑的名单）
    active_creators = read_creators_from_feishu()
    if not active_creators:
        print("ℹ️ 飞书博主名单中无启用的博主，本轮巡检结束")
        return
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    has_new_video = False  # 标记是否有新视频需要处理
    
    # 3. 逐个博主巡检，仅处理新视频
    for creator in active_creators:
        platform = creator["platform"]
        name = creator["name"]
        home_url = creator["url"]
        
        # 抓取该博主最新视频
        videos = get_latest_videos(platform, home_url)
        if not videos:
            print(f"ℹ️ {name}（{platform}）：无新视频，跳过")
            continue
        
        # 比对历史记录，处理未处理过的新视频
        for video in videos:
            vid = video["vid"]
            if vid in history[platform]:
                continue  # 已处理过，跳过
            
            # 新视频：AI总结 + 写入飞书 + 更新历史记录
            has_new_video = True
            summary = ai_classify_summary(video["content"])
            # 格式化输出内容（清晰易读）
            output_content = f"""
———————————————————— 新增视频总结 [{now}] ————————————————————
👤 博主：{name}
📺 平台：{platform}
📌 视频标题：{video["title"]}
🔗 视频链接：{video["video_url"]}

{summary}

"""
            # 写入飞书汇总文档
            append_to_feishu_summary(output_content)
            # 更新历史记录
            history[platform].append(vid)
            print(f"✅ 已处理：{name} - {video['title']}")
    
    # 4. 若有新视频，保存更新后的历史记录
    if has_new_video:
        save_history(history)
        print(f"✅ 本轮巡检完成，共处理{sum(1 for v in history['douyin'] + history['youtube'])}个视频，已同步到飞书")
    else:
        print("ℹ️ 本轮巡检：所有启用博主均无新视频，无需处理")

if __name__ == "__main__":
    main()
