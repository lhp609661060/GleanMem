from playwright.sync_api import sync_playwright
import json

import os
_HERE=os.path.dirname(os.path.abspath(__file__)); _ROOT=os.path.dirname(_HERE)
SPACE_KEY=json.load(open(os.path.join(_ROOT,'output','space_key.json')))['space_key']

# 基于真实对齐得到的初始事实（来自 2026-05-27 起的历史样本）
SEED_FACTS = [
    "任务背景：监控两个微信群的转发关系。来源群A=磐金-建翔业务沟通群(53423449835@chatroom)，"
    "目标群B=【内部】磐金协议分享(53531139660@chatroom)。需要预测A群哪些消息会被转发到B群。",
    "转发人规律：历史样本中主要转发人是「加一」(wxid_q2h2ni0pjj0322)，占绝大多数；"
    "其次建发销售付龙、张有为、A建发销售刘茂武偶发转发。",
    "转发内容规律：被转发的多为可直接执行的业务信息——车辆车牌与司机电话(如冀J9E712电话15131783123)、"
    "分货与规格可开单信息(如预分货76*4/159*4.5可开单)、新增资源吨位(如新增219*10*12米78吨)。"
    "转发基本不改写正文，偶尔补充「有人要吗」；闲聊和纯@提醒不会被转发。",
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # 直接用 space_key 登录进业务页（页面真实输入）
    page.goto("http://localhost:5173/#/login")
    page.wait_for_load_state("networkidle")
    page.locator("input[type=password]").fill(SPACE_KEY)
    page.locator("button", has_text="进入").click()
    page.wait_for_timeout(1200)
    page.screenshot(path="/tmp/wxkeywork/seed_space_home.png")

    # 从页面 sessionStorage 取 key（页面真实存储），构造 Bearer 头
    key = page.evaluate("() => sessionStorage.getItem('ydm_space_key')")
    print("page-held key prefix:", (key or '')[:10])

    for fact in SEED_FACTS:
        resp = page.evaluate(
            """async ({fact, key}) => {
              const r = await fetch('/api/v1/learning/events', {
                method: 'POST',
                headers: {'Content-Type':'application/json',
                          'Authorization': 'Bearer ' + key},
                body: JSON.stringify({type:'agent_mark', context: fact,
                                      marked_type:'fact', source:'example'})
              });
              return {status: r.status, body: await r.json()};
            }""",
            {"fact": fact, "key": key},
        )
        print("event:", resp)

    # 触发 flush（真实学习管线）
    flushed = page.evaluate(
        """async (key) => {
          const r = await fetch('/api/v1/learning/flush',
            {method:'POST', headers:{'Authorization':'Bearer '+key}});
          return {status: r.status, body: await r.json()};
        }""",
        key,
    )
    print("flush:", json.dumps(flushed, ensure_ascii=False))

    # 刷新记忆列表页并确认
    page.reload()
    page.wait_for_timeout(1500)
    page.screenshot(path="/tmp/wxkeywork/seed_memories.png", full_page=True)
    print("memories table text:")
    print(page.locator("body").inner_text()[:900])
    browser.close()
