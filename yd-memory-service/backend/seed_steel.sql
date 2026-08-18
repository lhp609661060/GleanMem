-- 钢材助手测试数据

-- 1. 钢材产品表
CREATE TABLE IF NOT EXISTS steel_products (
    id SERIAL PRIMARY KEY,
    product_code VARCHAR(50) NOT NULL UNIQUE,     -- 产品编码
    category VARCHAR(50) NOT NULL,                 -- 品类: 管材/板材/型材/线材
    material_type VARCHAR(50),                     -- 材质: 碳钢/不锈钢/合金钢
    grade VARCHAR(50) NOT NULL,                    -- 牌号: Q235B/20#/304/Q355B
    spec VARCHAR(200) NOT NULL,                    -- 规格: φ219×6/DN100
    unit VARCHAR(20) DEFAULT '吨',                 -- 单位
    length_m VARCHAR(50),                          -- 长度(m)
    weight_kg_per_m DECIMAL(10,3),                 -- 理论重量(kg/m)
    price_per_ton DECIMAL(12,2) NOT NULL,          -- 单价(元/吨)
    warehouse VARCHAR(100),                        -- 仓库
    origin VARCHAR(100),                           -- 产地
    stock_quantity DECIMAL(12,3) DEFAULT 0,        -- 库存量(吨)
    online_date DATE NOT NULL,                     -- 上线时间
    status VARCHAR(20) DEFAULT '在售',             -- 状态: 在售/停售/预售
    remark TEXT                                    -- 备注
);

-- 2. 价格政策表
CREATE TABLE IF NOT EXISTS steel_price_policy (
    id SERIAL PRIMARY KEY,
    policy_name VARCHAR(200) NOT NULL,
    policy_type VARCHAR(50) NOT NULL,              -- 批量折扣/会员价/区域价/促销
    category VARCHAR(50),                          -- 适用品类
    grade VARCHAR(50),                             -- 适用牌号
    min_quantity DECIMAL(12,3),                    -- 起订量(吨)
    discount_percent DECIMAL(5,2),                 -- 折扣(%)
    fixed_price DECIMAL(12,2),                     -- 固定价格
    start_date DATE NOT NULL,
    end_date DATE,
    status VARCHAR(20) DEFAULT '生效中',
    remark TEXT
);

-- 3. 订单表
CREATE TABLE IF NOT EXISTS steel_orders (
    id SERIAL PRIMARY KEY,
    order_no VARCHAR(50) NOT NULL UNIQUE,
    customer_name VARCHAR(200) NOT NULL,
    order_date DATE NOT NULL DEFAULT CURRENT_DATE,
    delivery_date DATE,
    total_amount DECIMAL(14,2),
    status VARCHAR(20) DEFAULT '待发货',           -- 待发货/已发货/已完成/已取消
    sales_person VARCHAR(100),
    remark TEXT
);

-- 4. 订单明细表
CREATE TABLE IF NOT EXISTS steel_order_items (
    id SERIAL PRIMARY KEY,
    order_id INT REFERENCES steel_orders(id),
    product_code VARCHAR(50),
    spec VARCHAR(200),
    grade VARCHAR(50),
    quantity DECIMAL(12,3) NOT NULL,               -- 数量(吨)
    unit_price DECIMAL(12,2) NOT NULL,             -- 成交单价
    amount DECIMAL(14,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    warehouse VARCHAR(100)
);

-- ===== 测试数据 =====

-- 钢材产品 (管材为主)
INSERT INTO steel_products (product_code, category, material_type, grade, spec, length_m, weight_kg_per_m, price_per_ton, warehouse, origin, stock_quantity, online_date, remark) VALUES
('GC-001', '管材', '碳钢', 'Q235B', 'φ219×6', '6-12', 31.52, 4250, '上海宝山仓', '宝钢', 156.8, '2025-01-15', '结构用无缝钢管'),
('GC-002', '管材', '碳钢', '20#', 'φ108×4.5', '6-9', 11.49, 4180, '上海宝山仓', '天津大无缝', 89.2, '2025-02-20', '流体输送管'),
('GC-003', '管材', '不锈钢', '304', 'φ57×3.5', '6', 4.62, 28500, '无锡仓', '太钢', 23.5, '2025-03-10', '食品级不锈钢管'),
('GC-004', '管材', '不锈钢', '316L', 'φ89×4', '6', 8.38, 45200, '无锡仓', '宝钢', 12.0, '2025-04-01', '耐腐蚀管'),
('GC-005', '管材', '碳钢', 'Q355B', 'φ159×8', '6-12', 29.79, 4680, '上海宝山仓', '鞍钢', 210.5, '2025-01-20', '低合金结构管'),
('GC-006', '管材', '碳钢', '20#', 'φ219×8', '6-12', 41.63, 4320, '广州仓', '包钢', 67.3, '2025-03-15', '高压锅炉管'),
('GC-007', '板材', '碳钢', 'Q235B', '10×2000×8000', NULL, NULL, 3980, '上海宝山仓', '宝钢', 320.0, '2025-02-01', '热轧板'),
('GC-008', '板材', '不锈钢', '304', '3×1500×6000', NULL, NULL, 26200, '无锡仓', '太钢', 45.0, '2025-03-20', '冷轧不锈钢板'),
('GC-009', '型材', '碳钢', 'Q235B', 'H200×200×8×12', '12', 50.5, 4150, '上海宝山仓', '马钢', 180.0, '2025-02-10', 'H型钢'),
('GC-010', '线材', '碳钢', 'HPB300', 'φ8', NULL, 0.395, 3880, '广州仓', '沙钢', 95.0, '2025-04-01', '盘螺');

-- 价格政策
INSERT INTO steel_price_policy (policy_name, policy_type, category, grade, min_quantity, discount_percent, start_date, end_date, remark) VALUES
('管材批量折扣', '批量折扣', '管材', NULL, 50, 3.0, '2025-01-01', '2025-12-31', '单次采购管材≥50吨享97折'),
('不锈钢大客户价', '会员价', '管材', '304', 10, 5.0, '2025-03-01', '2025-12-31', '304不锈钢管材≥10吨享95折'),
('华南区域促销', '区域价', '管材', 'Q235B', 5, 2.0, '2025-06-01', '2025-08-31', '华南区域Q235B管材额外优惠2%'),
('板材季度特惠', '促销', '板材', NULL, 20, 4.0, '2025-04-01', '2025-06-30', '板材季度促销94折');

-- 订单
INSERT INTO steel_orders (order_no, customer_name, order_date, delivery_date, total_amount, status, sales_person) VALUES
('SO-20250701', '上海建工集团', '2025-07-01', '2025-07-15', 216750.00, '已完成', '张三'),
('SO-20250702', '广州恒达管道', '2025-07-03', '2025-07-18', 125400.00, '已发货', '李四'),
('SO-20250703', '北京城建安装', '2025-07-05', '2025-07-20', 452800.00, '待发货', '王五'),
('SO-20250704', '深圳华强工程', '2025-07-08', '2025-07-22', 28500.00, '待发货', '李四'),
('SO-20250705', '杭州萧山钢构', '2025-07-10', '2025-07-25', 187600.00, '待发货', '张三');

-- 订单明细
INSERT INTO steel_order_items (order_id, product_code, spec, grade, quantity, unit_price, warehouse) VALUES
(1, 'GC-001', 'φ219×6', 'Q235B', 30, 4250, '上海宝山仓'),
(1, 'GC-005', 'φ159×8', 'Q355B', 20, 4680, '上海宝山仓'),
(1, 'GC-009', 'H200×200×8×12', 'Q235B', 5, 4150, '上海宝山仓'),
(2, 'GC-002', 'φ108×4.5', '20#', 20, 4180, '上海宝山仓'),
(2, 'GC-010', 'φ8', 'HPB300', 10, 3880, '广州仓'),
(3, 'GC-003', 'φ57×3.5', '304', 8, 28500, '无锡仓'),
(3, 'GC-004', 'φ89×4', '316L', 5, 45200, '无锡仓'),
(4, 'GC-003', 'φ57×3.5', '304', 1, 28500, '无锡仓'),
(5, 'GC-006', 'φ219×8', '20#', 30, 4320, '广州仓'),
(5, 'GC-001', 'φ219×6', 'Q235B', 15, 4250, '上海宝山仓');
