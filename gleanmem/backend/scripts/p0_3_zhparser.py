"""P0-3：tsvector + zhparser 中文召回验证（100 条记忆 × 20 个查询）。

用法（在 backend/ 目录）：
    docker compose -f ../docker-compose.yml up -d --build
    uv run alembic upgrade head
    uv run python scripts/p0_3_zhparser.py

判定标准（01-design §验证方案）：
    期望记忆出现在 Top3 的比例 ≥ 40%，且查询 p95 延迟 < 100ms；
    否则 V1 检索方案直接换 pgvector。

退出码：0 = 通过；1 = 未达标；2 = 前置条件不满足（zhparser 配置不存在等）。
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import asyncpg

DATABASE_URL = os.environ.get("YDM_DATABASE_URL", "postgresql://ydm:ydm@localhost:5432/ydm")
TEST_SPACE_NAME = "p03-zhparser-test"

# ---------------------------------------------------------------- 测试数据
# 10 个业务域 × 10 条记忆 = 100 条；title 唯一，content 为 1-2 句中文。
DOMAINS: dict[str, list[tuple[str, str]]] = {
    "发货": [
        ("发货单重量单位统一使用吨", "所有发货单的重量单位统一使用吨，不使用千克。"),
        ("发货单确认需要双人复核", "发货单确认异常时需要进行双人复核才能放行。"),
        ("发货前必须核对收货地址", "每次发货前必须与客户核对收货地址，防止错发。"),
        ("厦门客户发货统一使用顺丰快递", "厦门地区客户发货统一使用顺丰快递，其他地区默认德邦。"),
        ("发货单状态按四段流转", "发货单状态按待确认、待发货、已发货、已签收四段流转。"),
        ("发货逾期需要发送预警通知", "发货超过承诺时效时系统自动发送预警通知给客服。"),
        ("装车顺序按先重后轻", "装车顺序按先重后轻原则，重货在下轻货在上。"),
        ("发货回单必须回收存档", "发货回单必须在签收后七天内回收并存档。"),
        ("运费由收货方承担时需备注", "运费由收货方承担时发货单必须备注到付字样。"),
        ("发运计划每日十点生成", "发运计划每天上午十点自动生成，下午两点前可调整。"),
    ],
    "价格": [
        ("批量折扣按吨位阶梯计算", "批量折扣按采购吨位分三档阶梯计算。"),
        ("会员价按月销量自动调整", "会员价每月根据上月销量自动调整一次。"),
        ("区域价按销售大区设定", "区域价按华东、华南、华北、西南四个销售大区分别设定。"),
        ("促销价需要提前三天审批", "促销价格必须提前三天提交审批后才能生效。"),
        ("低于底价必须走特批流程", "任何低于底价的报价都必须走特批流程并留痕。"),
        ("涨价通知需要提前一周发送", "涨价需要提前一周书面通知所有签约客户。"),
        ("合同价锁定一个季度", "合同价格锁定一个季度，季度内不随市场价波动。"),
        ("一口价商品不允许议价", "一口价商品在系统里标记后不允许销售员议价。"),
        ("阶梯价按累计采购量计算", "阶梯价按客户累计采购量计算而不是单笔订单量。"),
        ("特价申请单需要财务会签", "特价申请单需要销售总监和财务双重会签。"),
    ],
    "客户": [
        ("客户按年采购额分为四级", "客户按年采购额分为战略、重点、普通、散户四级。"),
        ("信用额度按月滚动评估", "客户信用额度每月根据回款情况滚动评估一次。"),
        ("黑名单客户禁止新建订单", "进入黑名单的客户系统自动禁止新建订单。"),
        ("战略客户享受优先排产", "战略客户订单享受优先排产和优先发货。"),
        ("回款周期默认三十天", "标准回款周期为三十天，战略客户可放宽到六十天。"),
        ("客户归属按首次录入人确定", "客户归属按首次录入人确定，变更需要区域经理审批。"),
        ("新客户需要资质审核", "新客户必须提交营业执照等资质审核后才能下单。"),
        ("客户改名需要保留历史映射", "客户改名后系统保留新旧名称的历史映射关系。"),
        ("月度对账单五号前发送", "每月五号前向所有活跃客户发送上月对账单。"),
        ("每个客户指定唯一对接窗口", "每个客户指定唯一对接窗口，减少多头沟通。"),
    ],
    "仓库": [
        ("库位按品类分区管理", "仓库库位按管材、板材、型材分区管理。"),
        ("出库遵循先进先出原则", "所有出库操作遵循先进先出原则，禁止挑货。"),
        ("库存盘点每月一次", "库存盘点每月最后一天进行，差异超过百分之一需要复盘。"),
        ("安全库存按三十天销量设定", "安全库存按最近三十天平均销量的两倍设定。"),
        ("滞销库存超过九十天需要处理", "滞销库存超过九十天自动生成处理建议。"),
        ("出入库单必须当日录系统", "出入库单必须在业务发生当日录入系统。"),
        ("货损需要拍照留证", "发现货损必须现场拍照留证并登记货损单。"),
        ("冻结仓位不允许任何操作", "冻结仓位在解冻前不允许任何出入库操作。"),
        ("退货入库单独存放待检", "退货入库货物单独存放等待质量检验。"),
        ("库存上限按库容百分之八十控制", "单库库存总量不得超过库容的百分之八十。"),
    ],
    "物流": [
        ("承运商按季度考核评分", "承运商按季度考核，评分低于八十分的需要整改。"),
        ("运单必须当日录入跟踪号", "发货后运单跟踪号必须当日录入系统。"),
        ("时效承诺按区域分档", "物流时效承诺按区域分档：同省次日达、跨省三日达。"),
        ("破损索赔需要七十二小时申报", "货物破损索赔必须在签收后七十二小时内申报。"),
        ("签收异常需要客户证明", "签收异常时索赔需要客户提供签收证明文件。"),
        ("运费结算按月对账", "承运商运费每月十五号对账，二十号付款。"),
        ("长途路线必须购买运输保险", "超过五百公里的运输必须购买运输保险。"),
        ("拼车需提前一天预约", "零担拼车需要提前一天预约，整车提前半天。"),
        ("异常件两小时内上报", "物流异常件必须在发现后两小时内上报客服。"),
        ("运输破损率纳入承运商考核", "运输破损率超过千分之三的承运商暂停合作。"),
    ],
    "合同": [
        ("合同金额超过五十万需要总经理审批", "合同金额超过五十万元的需要总经理审批。"),
        ("合同用印需要法务审核", "所有合同用印前必须经过法务审核。"),
        ("合同签订后两天内归档", "合同签订后必须在两天内扫描归档。"),
        ("合同到期前一个月提醒续签", "合同到期前一个月系统自动提醒销售续签。"),
        ("合同终止需要书面确认", "合同终止必须双方书面确认后才能关闭。"),
        ("标准合同使用统一模板", "常规销售使用统一模板合同，法务审核过。"),
        ("合同金额变更需要补充协议", "合同金额变更需要签订补充协议而不是口头约定。"),
        ("合同附件必须完整上传", "合同附件包括资质文件，必须完整上传系统。"),
        ("签约主体必须与发票抬头一致", "合同签约主体必须与发票抬头完全一致。"),
        ("合同生效日期以盖章为准", "合同生效日期以最后一方盖章日期为准。"),
    ],
    "发票": [
        ("发票在发货后三天内开具", "发票在货物发出后三个工作日内开具。"),
        ("红冲发票需要原票收回", "红冲发票必须收回原发票后才能开具。"),
        ("发票抬头错误当日作废重开", "发票抬头开错的需要当日作废并重新开具。"),
        ("增值税专票税率十三点", "钢材销售增值税专用发票税率为百分之十三。"),
        ("专票和普票按客户资质区分", "一般纳税人开专票，小规模纳税人开普票。"),
        ("单张发票限额十万", "单张发票限额十万元，超过的需要拆分。"),
        ("发票邮寄使用顺丰保价", "发票邮寄统一使用顺丰快递并保价。"),
        ("作废发票需要主管签字", "作废发票需要财务主管签字确认。"),
        ("发票备注栏填写合同号", "发票备注栏必须填写对应的合同编号。"),
        ("开票申请当天处理完毕", "开票申请必须在收到当天处理完毕。"),
    ],
    "结算": [
        ("账期从开票之日起计算", "账期从开票之日起计算，不是从发货日起算。"),
        ("对账单需要客户盖章回传", "每月对账单需要客户盖章回传才算有效。"),
        ("回款核销按先进先出", "回款核销按账龄先进先出原则核销。"),
        ("预付款余额不足禁止发货", "预付款客户余额不足时系统禁止发货。"),
        ("保证金按合同金额百分之十收取", "保证金按合同金额的百分之十收取。"),
        ("尾款在验收后三十天内支付", "尾款在客户验收合格后三十天内支付。"),
        ("逾期回款按日万分之五计息", "逾期回款按日万分之五计算利息。"),
        ("结算单由财务统一出具", "结算单由财务统一出具，销售不得私自出具。"),
        ("争议金额冻结暂不结算", "有争议的金额先冻结，争议解决后再结算。"),
        ("关账日为每月二十五号", "每月二十五号为关账日，之后不再受理当月结算。"),
    ],
    "质量": [
        ("质检标准按国标执行", "钢材质量检验按国家标准执行，特殊要求合同注明。"),
        ("不合格品单独标识隔离", "不合格品必须单独标识并隔离存放。"),
        ("复检申请三个工作日内完成", "客户复检申请必须在三个工作日内完成。"),
        ("供应商按季度质量评分", "供应商按季度进行质量评分，低于八十分限采。"),
        ("客诉四十八小时内响应", "质量客诉必须在四十八小时内给出初步响应。"),
        ("质量索赔需要质检报告", "质量索赔必须附第三方质检报告。"),
        ("批次追溯码随货同行", "每批货物都有唯一追溯码，随货同行。"),
        ("出厂检验报告随货附带", "每批货物出厂必须附带检验报告。"),
        ("抽检比例按百分之十执行", "入库抽检比例按每批百分之十执行。"),
        ("让步接收需要技术部门签字", "让步接收必须技术部门和质量部门双签字。"),
    ],
    "售后": [
        ("退换货申请七天无理由", "客户可在签收后七天内申请无理由退换货。"),
        ("售后补偿按订单金额百分之一", "售后补偿标准为订单金额的百分之一。"),
        ("售后响应时效为两小时", "售后工单响应时效为两小时，超时自动升级。"),
        ("售后工单七天结案率考核", "售后工单七天结案率纳入客服考核。"),
        ("保修期按合同约定执行", "产品保修期按合同约定执行，一般为一年。"),
        ("质量责任认定需要三方确认", "质量责任认定需要客户、物流、工厂三方确认。"),
        ("退款原路退回七个工作日", "退款按原支付路径退回，七个工作日内到账。"),
        ("上门服务提前一天预约", "需要上门服务的提前一天与客户预约。"),
        ("差评处理四十八小时闭环", "电商差评必须在四十八小时内处理闭环。"),
        ("售后结案需要客户确认", "售后工单结案必须得到客户确认。"),
    ],
}

# 20 个查询（每域 2 个），每个查询期望命中对应标题。
QUERIES: list[tuple[str, str]] = [
    ("发货单重量用什么单位", "发货单重量单位统一使用吨"),
    ("厦门客户发什么快递", "厦门客户发货统一使用顺丰快递"),
    ("批量折扣怎么算", "批量折扣按吨位阶梯计算"),
    ("低于底价怎么办", "低于底价必须走特批流程"),
    ("客户分几个等级", "客户按年采购额分为四级"),
    ("回款周期多长", "回款周期默认三十天"),
    ("出库按什么顺序", "出库遵循先进先出原则"),
    ("盘点多久一次", "库存盘点每月一次"),
    ("货物破损怎么索赔", "破损索赔需要七十二小时申报"),
    ("长途运输要买保险吗", "长途路线必须购买运输保险"),
    ("合同金额多少要总经理批", "合同金额超过五十万需要总经理审批"),
    ("合同到期前做什么", "合同到期前一个月提醒续签"),
    ("发票什么时候开", "发票在发货后三天内开具"),
    ("红冲发票怎么处理", "红冲发票需要原票收回"),
    ("账期从哪天起算", "账期从开票之日起计算"),
    ("关账日是哪天", "关账日为每月二十五号"),
    ("质检按什么标准", "质检标准按国标执行"),
    ("不合格品怎么处理", "不合格品单独标识隔离"),
    ("退换货多久可以申请", "退换货申请七天无理由"),
    ("退款多久到账", "退款原路退回七个工作日"),
]

TOPK = 3
HIT_RATE_THRESHOLD = 0.40
P95_MS_THRESHOLD = 100.0


async def main() -> None:
    conn = await asyncpg.connect(DATABASE_URL, timeout=10)

    # 1. 前置检查：zhparser 配置必须存在（三环修复后）
    has_cfg = await conn.fetchval(
        "SELECT 1 FROM pg_ts_config WHERE cfgname = 'zhparser'"
    )
    if not has_cfg:
        print(
            "[前置失败] pg_ts_config 里没有 'zhparser' 配置。\n"
            "  请先完成 zhparser 三环修复（镜像 / CREATE EXTENSION+CONFIGURATION / 触发器配置）\n"
            "  并确认 alembic upgrade head 已成功执行。"
        )
        await conn.close()
        raise SystemExit(2)

    # 2. 清理旧测试数据，重建测试 Space 与 100 条记忆
    await conn.execute("DELETE FROM long_term_memories WHERE agent_id IN (SELECT agent_id FROM agent_spaces WHERE name = $1)", TEST_SPACE_NAME)
    await conn.execute("DELETE FROM agent_spaces WHERE name = $1", TEST_SPACE_NAME)
    agent_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO agent_spaces (agent_id, name, description) VALUES ($1, $2, 'P0-3 验证')",
        agent_id, TEST_SPACE_NAME,
    )

    rows = []
    for domain, items in DOMAINS.items():
        for idx, (title, content) in enumerate(items):
            rows.append(
                (str(uuid.uuid4()), agent_id, "reference", title, content, 1.0 - idx * 0.05)
            )
    await conn.executemany(
        "INSERT INTO long_term_memories (id, agent_id, type, title, content, weight, review_status) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'approved')",
        rows,
    )

    # 3. 触发器检查：search_vector 必须已填充
    null_vec = await conn.fetchval(
        "SELECT count(*) FROM long_term_memories WHERE agent_id = $1 AND search_vector IS NULL",
        agent_id,
    )
    if null_vec:
        print(f"[前置失败] {null_vec} 条记忆的 search_vector 为 NULL，触发器未生效。")
        await conn.close()
        raise SystemExit(2)

    # 4. 跑 20 个查询：AND 优先、空结果降级 OR（与应用 pg_store.search 逻辑一致）
    hits_top3 = 0
    hits_top1 = 0
    latencies: list[float] = []
    detail: list[str] = []

    SEARCH_SQL = """
        SELECT title, ts_rank(search_vector, {tsq}::tsquery) AS rank
        FROM long_term_memories
        WHERE agent_id = $2 AND search_vector @@ {tsq}::tsquery
        ORDER BY rank DESC
        LIMIT $3
    """

    for query, expected in QUERIES:
        t0 = time.perf_counter()
        and_tsq = await conn.fetchval(
            "SELECT plainto_tsquery('zhparser', $1)::text", query
        )
        results = await conn.fetch(SEARCH_SQL.format(tsq="$1"), and_tsq, agent_id, TOPK)
        if not results:
            or_tsq = await conn.fetchval(
                "SELECT to_tsquery('zhparser', replace(plainto_tsquery('zhparser', $1)::text, ' & ', ' | '))::text",
                query,
            )
            results = await conn.fetch(SEARCH_SQL.format(tsq="$1"), or_tsq, agent_id, TOPK)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)

        titles = [r["title"] for r in results]
        in_top3 = expected in titles
        in_top1 = bool(titles) and titles[0] == expected
        hits_top3 += in_top3
        hits_top1 += in_top1
        detail.append(
            f"  {'✅' if in_top3 else '❌'} [{query}] 期望[{expected}]"
            f" 实际Top3={titles[:3]}  {elapsed_ms:.1f}ms"
        )

    latencies.sort()
    p95_ms = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0
    hit_rate = hits_top3 / len(QUERIES)

    # 5. 报告
    print("=" * 72)
    print("P0-3：tsvector + zhparser 中文召回验证")
    print("=" * 72)
    for line in detail:
        print(line)
    print("-" * 72)
    print(f"记忆总数: 100   查询数: {len(QUERIES)}   TopK: {TOPK}")
    print(f"Top3 命中: {hits_top3}/{len(QUERIES)} = {hit_rate:.0%}   (阈值 ≥ {HIT_RATE_THRESHOLD:.0%})")
    print(f"Top1 命中: {hits_top1}/{len(QUERIES)}")
    print(f"p95 延迟: {p95_ms:.1f} ms   (阈值 < {P95_MS_THRESHOLD:.0f} ms)")
    print("-" * 72)
    if hit_rate >= HIT_RATE_THRESHOLD and p95_ms < P95_MS_THRESHOLD:
        print("结论: ✅ 通过 —— 继续 tsvector + zhparser 路线")
        await conn.close()
        raise SystemExit(0)
    else:
        print("结论: ❌ 未达标 —— 按 01-design 决策切换 pgvector（V1 排期 +1~2 周）")
        await conn.close()
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
