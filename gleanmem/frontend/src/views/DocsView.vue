<script setup>
/**
 * 文档（admin 平台管理台）：集成指南。
 * 当前两篇：Dify（MCP SSE + Flush workflow）、DSH（REST 直连）。
 */
import { ref } from 'vue'

const tab = ref('dify')
</script>

<template>
  <div class="head">
    <h1>文档</h1>
    <div class="row">
      <button :class="{ primary: tab === 'dify' }" @click="tab = 'dify'">Dify 集成</button>
      <button :class="{ primary: tab === 'dsh' }" @click="tab = 'dsh'">DSH 集成</button>
    </div>
  </div>

  <!-- ===================== Dify ===================== -->
  <div v-if="tab === 'dify'" class="docs">
    <div class="card">
      <h2>如何在 Dify 中使用</h2>
      <p class="muted">
        Dify 通过 <span class="mono">MCP（SSE）</span> 接入，Agent 直接调用
        <span class="mono">recall / load_memory / memorize</span> 三个工具；
        对话结束再用一个 HTTP 节点触发 <span class="mono">flush</span> 完成学习入库。
      </p>

      <h3>前置条件</h3>
      <ul>
        <li>gleanmem 已启动（本机默认 <span class="mono">http://127.0.0.1:8000</span>）。</li>
        <li>已有一个 Space，拿到两个标识：<span class="mono">agent_id</span>（UUID）和 <span class="mono">space_key</span>（<span class="mono">ydm_…</span>）。</li>
      </ul>

      <h3>第 1 步：配置 MCP Server</h3>
      <p>在 Dify 的 Agent 应用（Chatflow / Workflow）里添加一个 MCP Server：</p>
      <table>
        <thead><tr><th>项</th><th>值</th></tr></thead>
        <tbody>
          <tr><td>类型</td><td>SSE</td></tr>
          <tr><td>URL</td><td><span class="mono">http://&lt;host&gt;:8000/mcp/sse</span></td></tr>
          <tr><td>Headers</td><td><span class="mono">X-Agent-ID: &lt;agent_id&gt;</span></td></tr>
        </tbody>
      </table>
      <p class="muted">
        注意：Dify 的 MCP 注册只支持自定义 header、不支持 Bearer 鉴权，所以身份走
        <span class="mono">X-Agent-ID</span>（与 REST 的 space_key 解析到同一个 agent_id）。
      </p>

      <h3>第 2 步：三个工具</h3>
      <table>
        <thead><tr><th>工具</th><th>用途</th><th>关键参数</th></tr></thead>
        <tbody>
          <tr><td class="mono">recall</td><td>检索与当前任务相关的历史记忆 / 业务规则</td><td><span class="mono">intent</span>（自然语言描述需要什么）</td></tr>
          <tr><td class="mono">load_memory</td><td>加载某条记忆的完整内容</td><td><span class="mono">id</span>（来自 recall 返回）</td></tr>
          <tr><td class="mono">memorize</td><td>提交值得长期记住的信息</td><td><span class="mono">type</span>（user_feedback / agent_mark）、<span class="mono">context</span></td></tr>
        </tbody>
      </table>

      <h3>第 3 步：配置 Flush 节点（对话结束触发学习）</h3>
      <p>Workflow 结构：<span class="mono">Start → Agent（配 MCP）→ HTTP Request → End</span>。</p>
      <p>HTTP 节点配置：</p>
      <table>
        <thead><tr><th>项</th><th>值</th></tr></thead>
        <tbody>
          <tr><td>方法</td><td><span class="mono">POST</span></td></tr>
          <tr><td>URL</td><td><span class="mono">http://&lt;host&gt;:8000/api/v1/learning/flush</span></td></tr>
          <tr><td>Header</td><td><span class="mono">Authorization: Bearer &lt;space_key&gt;</span></td></tr>
          <tr><td>Body</td><td v-pre><pre>{"session_id": "{{sys.conversation_id}}"}</pre></td></tr>
        </tbody>
      </table>
      <p class="muted">
        Flush 用 API Key 而非 X-Agent-ID，是因为 Dify 的 HTTP 节点支持自定义 header，可以走 Bearer。
      </p>

      <h3>快速开始</h3>
      <p>
        仓库根目录提供现成模板 <span class="mono">测试.yml</span>（记忆增强助手：Start → Agent → Flush），
        可直接导入 Dify，再填上 MCP URL / X-Agent-ID / space_key 即可。
      </p>
    </div>
  </div>

  <!-- ===================== DSH ===================== -->
  <div v-else class="docs">
    <div class="card">
      <h2>如何在 DSH 中安装和使用</h2>
      <p class="muted">
        gleanmem 是<b>独立部署的服务</b>，不是 DSH 插件——DSH 侧无需「安装」，只需通过
        <span class="mono">REST</span> 接入（或 MCP client 复用 SSE 端点）。三个接口覆盖「检索 / 提交 / 学习」。
      </p>

      <h3>前置条件</h3>
      <ul>
        <li>服务已启动（本机默认 <span class="mono">http://127.0.0.1:8000</span>）。</li>
        <li>已有一个 Space 的 <span class="mono">space_key</span>（在「空间管理」里创建，或 <span class="mono">POST /api/v1/spaces</span>）。</li>
      </ul>

      <h3>三个 REST 接口</h3>
      <table>
        <thead><tr><th>动作</th><th>接口</th><th>说明</th></tr></thead>
        <tbody>
          <tr><td>检索记忆</td><td class="mono">POST /api/v1/recall</td><td>body：<span class="mono">{"intent": "…"}</span></td></tr>
          <tr><td>提交记忆</td><td class="mono">POST /api/v1/learning/events</td><td>body：<span class="mono">{"type": "…", "context": "…"}</span></td></tr>
          <tr><td>触发学习</td><td class="mono">POST /api/v1/learning/flush</td><td>对话结束统一分析入库</td></tr>
        </tbody>
      </table>
      <p class="muted">所有请求都带 <span class="mono">Authorization: Bearer &lt;space_key&gt;</span>（身份从 key 解析，不在 body 里）。</p>

      <h3>curl 示例</h3>
      <pre># 检索
curl -X POST http://127.0.0.1:8000/api/v1/recall \
  -H "Authorization: Bearer $SPACE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"intent": "发货单重量用什么单位"}'

# 提交
curl -X POST http://127.0.0.1:8000/api/v1/learning/events \
  -H "Authorization: Bearer $SPACE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"type": "user_feedback", "context": "厦门客户发货用顺丰"}'

# 触发学习（对话结束）
curl -X POST http://127.0.0.1:8000/api/v1/learning/flush \
  -H "Authorization: Bearer $SPACE_KEY"</pre>

      <h3>在 DSH 里怎么调用</h3>
      <ul>
        <li>
          <b>方式 A（推荐，若 DSH 支持 MCP client）</b>：复用 SSE 端点
          <span class="mono">http://&lt;host&gt;:8000/mcp/sse</span>，header 填
          <span class="mono">X-Agent-ID: &lt;agent_id&gt;</span>，即可获得与 Dify 相同的
          <span class="mono">recall / load_memory / memorize</span> 三工具。
        </li>
        <li>
          <b>方式 B（REST 直连）</b>：在 DSH 侧配一个记忆 skill / 工具，把上面的三个 curl
          封装成「检索」「记忆」「flush」三个动作，Agent 需要时调用。
        </li>
      </ul>

      <h3>完整示例</h3>
      <p>服务端自带端到端演示（创建 Space → 提交 → flush → 检索 → 隔离验证）：</p>
      <pre>cd gleanmem/backend
uv run python scripts/e2e_demo.py http://127.0.0.1:8000</pre>
    </div>
  </div>
</template>
