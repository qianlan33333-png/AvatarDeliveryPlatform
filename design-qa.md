# Avatar Delivery Platform 全量 UI 重构 Design QA

## 对比基准

- 视觉与交互唯一来源：`/Users/qianlan/Downloads/Avatar Delivery Platform.zip`
- 实施规范：压缩包内 `AI-BUILD-SPEC.md`
- 后台参考截图：`/private/tmp/avatar-design-admin-reference.png`
- 后台实现截图：`design-admin-implementation.jpg`
- 小程序参考截图：`/private/tmp/avatar-design-miniprogram-reference.png`
- 对比视口：后台 `1400 × 1000`；小程序按微信 375px 逻辑宽度及 rpx 规则核对。

## 同视口视觉比较

后台参考与实现已在同一次图像输入中并排比较。实现保留了设计包的纸张灰侧栏、白色内容面、陶土橙主操作、衬线标题、mono 辅助信息、12px 卡片和 8px 控件圆角；导航密度、搜索区、表格表头、状态徽标和分页位置与参考一致。未发现 P0 / P1 / P2 视觉偏差。

小程序逐项按参考页和构建规范核对：全局底色、能力卡、课程卡、详情进度、课节列表、播放器倍速条、AI 气泡、推荐卡、输入区、个人中心和纯文字 TabBar 均使用同一令牌，不保留旧黄色或渐变。微信开发者工具已成功打开本地项目，TypeScript 编译通过；本轮未向微信外部服务上传预览包。

## 页面与交互验收

- 后台一级导航严格为 6 项；商品映射与 AI 会员位于侧栏底部。
- 用户列表仅保留“用户明细”；详情页合并课程权益与 AI 会员权益。
- 课程列表包含学员数和分页；课程编辑为基础信息、课节与视频、发布检查三步。
- 视频素材保留 VOD 直传、状态轮询和课程绑定入口。
- 开通记录只保留查询、事件列表、人工重放和真分页。
- 分身语料拥有来源侧栏、5 列列表和分页；知识导入与审核逻辑不变。
- 模型配置页不再暴露用途绑定表单和路由。
- 小程序 TabBar 为纯文字 17px；首页双能力入口、课程列表、详情、播放、问答、话术和我的均已换肤。
- 话术回复支持“再短一点 / 换个更口语的开头 / 复制”；问答继续支持图片和课程推荐卡。
- 播放容量满时显示在线人数，并明确已有用户不受影响。

## 自动化证据

- `ruff check backend tests`：通过。
- `.venv/bin/python -m pytest -q`：91 项通过。
- 根目录 `npm run typecheck`：通过。
- `miniprogram/npm run typecheck`：通过。
- 本地后台七个入口和课程编辑三段均完成浏览器路由回归，无 500。
- 全站搜索不到 `#FDF8E7`、`#F5D060`、`--yellow` 和 `linear-gradient`。

## Findings

- [P3] 课程封面本地选择仍缺正式对象存储直传接口。
  - 设计壳、文件体积、尺寸、比例校验和 CDN URL 回填入口已经保留。
  - 缺少后端一次性对象存储签名契约，未伪造上传成功状态。
- [P3] 播放容量错误没有真实请求 ID。
  - 服务端已返回在线人数，客户端已接入；当前请求链没有 request ID 字段，因此未在 UI 伪造。

final result: passed
