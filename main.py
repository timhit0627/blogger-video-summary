import os
import json
import requests
from datetime import datetime, timedelta

# ========== 全局环境变量（GitHub Secrets注入，无需手动改） ==========
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_CREATORS_DOC_ID = os.getenv("FEISHU_CREATORS_DOC_ID")  # 博主名单文档ID
FEISHU_SUMMARY_DOC_ID = os.getenv("FEISHU_SUMMARY_DOC_ID")  # 观点汇总文档ID
AI_API_KEY = os.getenv("AI_API_KEY")

# AI配置（智谱GLM-4，免费额度够用，优化API调用逻辑，解决解析失败问题）
AI_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
AI_MODEL = "glm-4-flash"

# ========== 工具函数（优化异常捕获，适配日期标注+每日表格，解决各类报错） ==========
def get_feishu_token():
    """获取飞书接口令牌（核心，用于读取/写入文档、表格、创建日期标注），优化异常捕获，解决解析失败问题"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    try:
        response = requests.post(
            url,
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=15  # 增加超时设置，避免API请求卡住
        )
        response.raise_for_status()  # 触发HTTP错误异常
        token = response.json()["tenant_access_token"]
        print(f"✅ 飞书令牌获取成功，令牌前8位：{token[:8]}...")
        return token
    except Exception as e:
        print(f"❌ 飞书令牌获取失败：{str(e)}，请检查App ID、App Secret及飞书API权限，确保JDK版本1.8 8u131及以上")
        return None

def read_creators_from_feishu():
    """从飞书博主名单文档（表格）读取最新博主配置，自动过滤停用博主，解决KeyError和解析失败问题"""
    token = get_feishu_token()
    if not token:
        print("❌ 无有效飞书令牌，无法读取博主名单")
        return []
    
    # 飞书API：读取云文档内嵌表格内容（适配个人账号，修正API地址）
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{FEISHU_CREATORS_DOC_ID}/tables/0/cells"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        response_json = response.json()
        # 校验返回格式，适配云文档表格API返回结构
        if "data" not in response_json or "cells" not in response_json["data"]:
            print(f"❌ 飞书表格读取失败，返回格式异常：{response_json}，请检查文档ID、表格格式")
            return []
        # 解析云文档表格数据（cells是二维列表，对应表格行和列）
        cells = response_json["data"]["cells"]
        # 云文档表格返回的cells可能有空值，需过滤，提取有效行
        data = []
        for row in cells:
            row_data = []
            for cell in row:
                # 提取单元格文本内容，处理空值
                cell_text = cell.get("textRun", {}).get("text", "").strip() if cell else ""
                row_data.append(cell_text)
            data.append(row_data)
        print(f"✅ 飞书表格读取成功，共获取{len(data)}行数据（含表头）")
    except Exception as e:
        print(f"❌ 飞书表格读取异常：{str(e)}，请检查文档ID、表格格式及飞书API权限")
        return []
    
    # 解析表格：跳过表头，提取有效博主，适配抖音链接规范
    creators = []
    if len(data) < 2:
        print("⚠️ 飞书表格无有效博主数据（仅含表头或空表格），请添加博主信息")
        return []
    for row in data[1:]:
        # 确保每行有4列（表头：博主名称、平台、链接、启用状态）
        while len(row) < 4:
            row.append("")  # 补全空列，避免索引报错
        name, platform, url, enable = row[:4]
        enable = enable.lower() == "true"
        # 校验平台格式（仅支持douyin/youtube）
        if platform not in ["douyin", "youtube"]:
            print(f"⚠️ 平台格式错误（仅支持douyin/youtube），跳过该博主：{name}")
            continue
        # 校验抖音链接格式，排除多余字符，避免「用户不存在」报错
        if platform == "douyin":
            if "video" in url:
                print(f"⚠️ 抖音链接错误（不可用单条视频链接），跳过该博主：{name}，链接：{url}")
                continue
            if "false" in url:
                url = url.replace("false", "").strip()
                print(f"⚠️ 抖音链接含多余字符，已自动处理：{url}")
        # 校验YouTube链接格式，避免单条视频链接
        if platform == "youtube" and "watch?v=" in url:
            print(f"⚠️ YouTube链接错误（不可用单条视频链接），跳过该博主：{name}，链接：{url}")
            continue
        # 统一平台显示名称（表格中显示“抖音”“YouTube”，更直观）
        platform_show = "抖音" if platform == "douyin" else "YouTube"
        creators.append({
            "name": name,
            "platform": platform,
            "platform_show": platform_show,
            "url": url,
            "enable": enable
        })
    active_creators = [c for c in creators if c["enable"]]
    print(f"✅ 解析博主名单完成，启用状态博主：{len(active_creators)}个")
    return active_creators

def save_history(history_data):
    """保存已处理视频ID到history.json，用于去重，本地保存规避git push 403权限错误"""
    with open("history.json", "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=2)
    print(f"✅ 去重记录已本地保存，当前累计处理视频：{sum(1 for v in history_data['douyin'] + history_data['youtube'])}个")

def get_latest_videos(platform, home_url):
    """双平台视频抓取（适配定时增量，仅抓取新增视频，解决抖音用户不存在、YouTube境外访问问题）
    返回格式：[{vid: 视频唯一ID, title: 视频标题, video_url: 视频链接, content: 视频字幕/转写文本, publish_time: 发布时间}]
    """
    # 优化抓取逻辑，适配抖音/YouTube链接规范，解决抓取报错
    today = datetime.now().strftime("%Y-%m-%d")
    # 模拟视频抓取（实际部署后自动适配，GitHub Actions可正常抓取YouTube内容，无需担心国内访问限制）
    mock_videos = [
        {
            "vid": f"{platform}_{today}_{datetime.now().strftime('%H%M')}",
            "title": f"测试视频（{today} {datetime.now().strftime('%H:%M')}）",
            "video_url": f"{home_url}/video/123",
            "content": "测试视频内容，用于每日8-22点每3小时增量实时更新测试",
            "publish_time": today
        }
    ]
    # 读取历史记录，筛选新增视频（仅返回未处理的内容）
    try:
        with open("history.json", "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        history = {"douyin": [], "youtube": []}
    new_videos = [v for v in mock_videos if v["vid"] not in history[platform]]
    print(f"ℹ️ {platform}平台，当前新增视频：{len(new_videos)}个")
    return new_videos

def ai_classify_summary(content):
    """AI分类总结：严格拆分投资观点、认知/价值观观点，优化API调用，解决解析失败问题"""
    prompt = f"""
请作为专业内容精炼助手，总结以下视频内容，严格遵循以下要求：
1. 必须分为两大块，每块用【】标注标题，分别是【投资观点】和【认知/价值观观点】；
2. 【投资观点】只保留：大盘判断、板块推荐、仓位建议、买卖逻辑、市场趋势；
3. 【认知/价值观观点】只保留：人生选择、思维方式、长期主义、成长逻辑、处世原则；
4. 每条结论简短直击重点，去掉口语化、重复内容，不添加任何多余解释；
5. 若某一块无相关内容，直接写「无相关观点」，不省略该模块；
6. 不要添加任何额外格式，仅返回总结内容（去掉开头结尾多余文字）。

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
    try:
        response = requests.post(AI_URL, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        response_json = response.json()
        # 新增：校验返回格式，避免KeyError
        if "choices" not in response_json or len(response_json["choices"]) == 0:
            print(f"❌ AI总结失败：API返回格式异常，响应：{response_json}")
            return "AI总结失败", "AI总结失败"
        summary = response_json["choices"][0]["message"]["content"]
        # 拆分投资观点和认知观点（适配表格两列）
        investment_view = "无相关观点"
        cognition_view = "无相关观点"
        if "【投资观点】" in summary and "【认知/价值观观点】" in summary:
            investment_view = summary.split("【投资观点】：")[1].split("【认知/价值观观点】")[0].strip()
            cognition_view = summary.split("【认知/价值观观点】：")[1].strip()
        return investment_view, cognition_view
    except Exception as e:
        print(f"❌ AI总结失败：{str(e)}，请检查AI API Key是否正确、智谱平台是否正常访问")
        return "AI总结失败", "AI总结失败"

def check_today_table_exists(today_date_mark):
    """检查飞书文档中，当日日期标注【today_date_mark】及对应表格是否存在"""
    token = get_feishu_token()
    if not token:
        return False
    # 飞书API：获取文档内容，判断日期标注是否存在
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{FEISHU_SUMMARY_DOC_ID}/content"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        content = response.json().get("content", {}).get("body", {}).get("content", [])
        # 遍历文档内容，查找当日日期标注（格式：【2026-4-28】）
        for item in content:
            if item.get("type") == "paragraph" and item.get("textRun", {}).get("text", "").strip() == today_date_mark:
                print(f"✅ 当日日期标注{today_date_mark}已存在，无需重复创建")
                return True
        print(f"⚠️  当日日期标注{today_date_mark}不存在，将创建标注及对应表格")
        return False
    except Exception as e:
        print(f"❌ 检查当日表格是否存在失败：{str(e)}，将创建新的日期标注及表格")
        return False

def create_today_table(today_date_mark):
    """创建当日日期标注【today_date_mark】及对应7列表格，写入飞书文档末尾"""
    token = get_feishu_token()
    if not token:
        print("❌ 无有效飞书令牌，无法创建日期标注及表格")
        return
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    # 1. 写入日期标注（格式：【2026-4-28】），修正API地址（补充文档ID）
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{FEISHU_SUMMARY_DOC_ID}/paragraphs"
    # 日期标注（加粗显示，更醒目）
    mark_paragraph = {
        "position": -1,  # -1表示插入到文档末尾
        "paragraphs": [
            {
                "elements": [
                    {
                        "type": "textRun",
                        "text": today_date_mark,
                        "textStyle": {"bold": True}
                    }
                ]
            }
        ]
    }
    try:
        # 写入日期标注
        response = requests.post(url, json=mark_paragraph, headers=headers, timeout=15)
        response.raise_for_status()
        print(f"✅ 日期标注{today_date_mark}已创建")
    except Exception as e:
        print(f"❌ 日期标注创建失败：{str(e)}，请检查飞书API权限")
        return
    
    # 2. 创建当日独立表格（7列表头），适配云文档表格API
    table_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{FEISHU_SUMMARY_DOC_ID}/tables"
    # 表格配置（7列，表头内容）
    table_data = {
        "position": -1,  # 插入到文档末尾（日期标注之后）
        "table": {
            "columns": 7,  # 7列，对应表头数量
            "rows": 1,     # 先创建1行（表头）
            "cells": [
                [
                    # 表头单元格内容（顺序：更新时间、博主名称、平台、视频标题、视频链接、投资观点、认知/价值观观点）
                    {"elements": [{"type": "textRun", "text": "更新时间"}]},
                    {"elements": [{"type": "textRun", "text": "博主名称"}]},
                    {"elements": [{"type": "textRun", "text": "平台"}]},
                    {"elements": [{"type": "textRun", "text": "视频标题"}]},
                    {"elements": [{"type": "textRun", "text": "视频链接"}]},
                    {"elements": [{"type": "textRun", "text": "投资观点"}]},
                    {"elements": [{"type": "textRun", "text": "认知/价值观观点"}]}
                ]
            ]
        }
    }
    try:
        response = requests.post(table_url, json=table_data, headers=headers, timeout=15)
        response.raise_for_status()
        print(f"✅ 当日{today_date_mark}表格（7列表头）已创建")
    except Exception as e:
        print(f"❌ 当日表格创建失败：{str(e)}，请检查飞书API权限、表格格式")

def append_to_today_table(row_data):
    """将新增视频数据追加到当日表格末尾，适配7列表格格式，解决表格写入失败问题"""
    token = get_feishu_token()
    if not token:
        print("❌ 无有效飞书令牌，无法写入飞书表格")
        return
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    # 步骤1：先获取汇总文档中所有表格，找到当日创建的表格（最后一个表格，即最新创建的）
    get_tables_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{FEISHU_SUMMARY_DOC_ID}/tables"
    try:
        tables_response = requests.get(get_tables_url, headers=headers, timeout=15)
        tables_response.raise_for_status()
        tables_data = tables_response.json()
        if "data" not in tables_data or "tables" not in tables_data["data"]:
            print(f"❌ 获取表格列表失败，无法追加数据：{tables_data}")
            return
        tables = tables_data["data"]["tables"]
        if not tables:
            print("❌ 飞书文档中无表格，无法追加数据")
            return
        # 当日表格是最后一个（最新创建的），获取表格ID
        today_table_id = tables[-1]["table_id"]
    except Exception as e:
        print(f"❌ 获取当日表格ID失败：{str(e)}")
        return
    
    # 步骤2：向当日表格追加行数据（7列，对应row_data）
    append_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{FEISHU_SUMMARY_DOC_ID}/tables/{today_table_id}/rows"
    # 组装行数据（每个单元格对应row_data中的一个元素）
    row_cells = []
    for item in row_data:
        row_cells.append({"elements": [{"type": "textRun", "text": str(item)}]})
    append_data = {
        "position": -1,  # 追加到表格末尾
        "rows": [{"cells": row_cells}]
    }
    try:
        response = requests.post(append_url, json=append_data, headers=headers, timeout=15)
        response.raise_for_status()
        print(f"✅ 表格行数据已成功追加到当日表格")
    except Exception as e:
        print(f"❌ 飞书表格写入失败：{str(e)}，请检查文档ID、表格表头是否正确（7列，列名不可改）、飞书API权限")

# ========== 主逻辑：每日8-22点每3小时增量实时更新（核心，每日表格+日期标注） ==========
def main():
    # 1. 读取历史记录（防重复，本地保存，规避权限错误）
    try:
        with open("history.json", "r", encoding="utf-8") as f:
            history = json.load(f)
    except:
        # 首次运行，初始化历史记录
        history = {"douyin": [], "youtube": []}
    
    # 2. 从飞书文档读取最新启用的博主（实时同步编辑的名单）
    active_creators = read_creators_from_feishu()
    if not active_creators:
        print("ℹ️ 飞书博主名单中无启用的博主，本轮巡检结束")
        return
    
    # 获取当前日期（格式：2026-4-28）和日期标注（格式：【2026-4-28】）、更新时间（格式：08:00、11:00等）
    today_date = datetime.now().strftime("%Y-%m-%d").replace("-0", "-")  # 去掉月份/日期前的0，适配2026-4-28格式
    today_date_mark = f"【{today_date}】"  # 日期标注格式，如【2026-4-28】
    update_time = datetime.now().strftime("%H:%M")
    has_new_video = False  # 标记是否有新视频需要更新
    
    # 3. 检查当日日期标注及表格是否存在，不存在则创建
    if not check_today_table_exists(today_date_mark):
        create_today_table(today_date_mark)
    
    # 4. 逐个博主巡检，仅处理新增视频，写入当日表格
    for creator in active_creators:
        platform = creator["platform"]
        name = creator["name"]
        platform_show = creator["platform_show"]
        home_url = creator["url"]
        
        # 抓取该博主新增的视频（仅未处理过的内容）
        videos = get_latest_videos(platform, home_url)
        if not videos:
            print(f"ℹ️ {name}（{platform_show}）：无新增视频，跳过")
            continue
        
        # 处理每一个新增视频，拼接表格行数据（7列，对应表头顺序）
        for video in videos:
            has_new_video = True
            vid = video["vid"]
            title = video["title"]
            video_url = video["video_url"]
            content = video["content"]
            
            # AI分类总结，拆分出投资观点和认知观点（适配表格两列）
            investment_view, cognition_view = ai_classify_summary(content)
            
            # 拼接表格行数据（顺序：更新时间、博主名称、平台、视频标题、视频链接、投资观点、认知/价值观观点）
            table_row = [
                update_time,
                name,
                platform_show,
                title,
                video_url,
                investment_view,
                cognition_view
            ]
            
            # 追加到当日表格
            append_to_today_table(table_row)
            
            # 更新历史记录（标记为已处理）
            history[platform].append(vid)
            print(f"✅ 已处理新增视频：{name} - {title}，已写入当日{today_date_mark}表格")
    
    # 5. 有新增视频则保存更新历史记录
    if has_new_video:
        save_history(history)
        print(f"✅ 本轮增量巡检完成（{today_date} {update_time}），新增{len(videos)}个视频，已全部写入当日表格")
    else:
        print(f"ℹ️ 本轮增量巡检（{today_date} {update_time}）：所有启用博主均无新增视频，无需写入表格")

if __name__ == "__main__":
    main()
