# 后续改进待办：开源项目可复用点

本文记录对 [MxPage](https://github.com/ziguishian/MxPage)、[OneMix](https://github.com/millerice/OneMix) 和 [EcomGen](https://github.com/linbei0/EcomGen) 的可借鉴设计。它们属于首版完成后的新工作范围；当前实现和已完成任务分别见 [系统设计](docs/system-design.md) 与 [OpenSpec 任务](openspec/changes/generate-product-detail-images/tasks.md)。执行某项前先建立对应的 OpenSpec 变更并细化接口与测试。

## P0：真实使用前的调用可靠性

- [ ] **P0-1 处理生图请求中断后的重复计费风险。** 在模块记录中保存稳定的 `attemptId`、实际请求提示词、请求发起时间和结果状态。服务重启时，已发起但未确认结果的请求进入“结果未知、需用户决定”状态，不自动再次调用；只有确认服务商支持幂等键的适配器才传幂等键。模拟“发起请求后、保存结果前崩溃”，验收重启不会自动重复生图，已完成图片仍可用。主要涉及 `server/services/job-runner.js`、`job-store.js`、`public/results.js`。参考 [EcomGen 的请求记录与恢复约定](https://github.com/linbei0/EcomGen/blob/main/ARCHITECTURE.md)。
- [ ] **P0-2 建立模型能力画像与创建任务前校验。** 以角色和模型 ID 描述是否支持图片输入、参考图编辑、数量、比例、尺寸与质量参数。任务创建前检查当前配置和所选比例；能力未知时提示先运行图片测试，能力不支持时阻止创建并给出具体原因。测试覆盖支持、不支持及未知模型。主要涉及 `server/services/profile-store.js`、`server/routes/models.js`、`jobs.js`、`server/providers/image-openai.js`、`public/settings.js`。参考 [EcomGen 的 Provider 能力边界](https://github.com/linbei0/EcomGen/blob/main/ARCHITECTURE.md)。

## P1：成套详情图质量与可控性

- [ ] **P1-1 把商品事实与创意推断分开。** 为分析结果增加“用户已确认事实、从原图可观察事实、推断、未知项”及来源；生成前允许用户核对。标题、参数和认证只能使用已确认事实，推断只作为可选视觉创意且不能写成商品承诺。测试模型推断尺寸、材质或认证时不会出现在确认文案中。主要涉及 `server/providers/vision-*.js`、`server/services/prompt-builder.js`、`public/script.js`。参考 [EcomGen 的 factClaims 约束](https://github.com/linbei0/EcomGen/blob/main/ARCHITECTURE.md)；[OneMix 的信息提取提示词](https://github.com/millerice/OneMix/blob/main/backend/onemix/services/prompt_templates.py)可作为需要显式标记推断的对照。
- [ ] **P1-2 增加生成前可审阅的整套方案。** 原图分析后一次性规划共享视觉风格（色彩、背景、光线、字体、留白、商品外观约束）及各模块的目标、构图和差异点，保存在任务快照。用户确认或修改方案后再逐图调用生图模型；验证不同模块共享风格，但标题与画面任务不重复。主要涉及 `server/services/job-runner.js`、`prompt-builder.js`、`job-store.js` 和新增方案审阅 UI。参考 [MxPage 的视觉风格与分屏规划](https://github.com/ziguishian/MxPage/blob/main/lib/ai/prompts/planning.ts)。
- [ ] **P1-3 支持模块级参考图、提示词和版本。** 每个模块可从本任务原图中选择主参考图与补充参考图，查看并修改最终生图提示词；单模块重做时保留前一版图片、提示词和模型参数，导出时明确选用的版本。测试不能引用其他任务的图片，重做不会覆盖历史版或触发其他模块。主要涉及 `server/routes/jobs.js`、`server/services/job-runner.js`、`job-store.js`、`public/results.js`。参考 [OneMix 的槽位参考图分配](https://github.com/millerice/OneMix/blob/main/backend/app/ref_paths.py)和 [EcomGen 的 promptInstruction / compiledPrompt](https://github.com/linbei0/EcomGen/blob/main/ARCHITECTURE.md)。

## P2：模板与批量交付

- [ ] **P2-1 建立轻量模块模板库。** 为现有 16 个模块定义稳定 ID、画面目标、必要事实、默认比例和不应出现的元素；规划时检查重复目标并允许用户覆盖。先用仓库内 JSON/JS 文件，不引入新的 Agent 运行时。测试不同品类不会套用不相关的尺寸、认证或使用场景。主要涉及 `server/services/prompt-builder.js`、`server/routes/jobs.js`。参考 [EcomGen 的模板与分镜角色](https://github.com/linbei0/EcomGen/blob/main/ARCHITECTURE.md)。
- [ ] **P2-2 扩展成套导出与批量 SKU。** 在现有逐图 ZIP 的基础上，评估长图拼接、手机预览和多个商品依次入队；每个商品保持独立任务、原图和费用预估。先明确目标平台的宽度、文字可读性与失败处理，再实现导出。主要涉及 `server/services/exporter.js`、任务列表和结果页。参考 [MxPage 的详情长图与批量流程](https://github.com/ziguishian/MxPage)及 [OneMix 的分类导出](https://github.com/millerice/OneMix)。

## 复用边界

- 优先复用**数据结构、交互和失败处理原则**，在本项目的 Node 服务中实现；三个仓库的完整运行栈与当前项目不同。
- OneMix 使用 GPL-3.0；直接复制其源码前先核对许可与本项目发布方式。MxPage、EcomGen 的 MIT 许可也需保留相应版权与许可声明。
- 保持当前 SK 仅在服务进程内存的策略；不照搬 MxPage 的浏览器 `localStorage` 密钥保存。参考 [MxPage 配置说明](https://github.com/ziguishian/MxPage)。
- 不照搬 OneMix 在图生图超时后自动回退文生图的行为；该回退可能使商品外观偏离参考图。参考 [OneMix 生图实现](https://github.com/millerice/OneMix/blob/main/backend/onemix/services/slot_batch_gen.py)。
