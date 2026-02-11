# Douyin Media Extractor (抖音媒体提取器)

`douyin_phaser.py` 是一个基于 Playwright 浏览器自动化的抖音媒体直链提取工具。支持提取： **视频 (4K)**、**图集** 和 **动图** 的直链提取。提供命令行工具 (CLI) 和 HTTP API 两种使用方式。

## ✨ 功能特点

- **全类型支持**: 视频 (`/video/`)、图文 (`/note/`)、动图 (`/note/` + GIF)
- **最高画质**: 自动选择 4K/2K/1080p 最高清晰度
- **API 服务**: 内置 FastAPI 服务器，方便集成到其他系统
- **反爬虫绕过**: 基于 Playwright 模拟真实浏览器，自动处理签名和验证

## ⚠️ 免责声明

1. 本项目仅供技术研究和学习交流使用，请勿用于非法用途。
2. 用户在使用本工具时，必须遵守当地法律法规。对于用户因非法使用而产生的任何法律纠纷，作者不承担任何责任。
3. 本工具仅提供解析功能，不存储任何视频资源。所有视频版权归原平台及作者所有。
4. 如果原平台认为本项目侵犯了您的权益，请联系我，我会立即处理。 📧


## 🚀 快速开始

### 方式一：Docker 部署 (推荐)

无需安装 Python 环境，直接拉取并运行镜像：

```bash
# 拉取镜像
docker pull ghcr.io/yusongxiao/douyin_phaser:main

# 或者使用镜像仓库
docker pull ccr.ccs.tencentyun.com/songhappy/douyin_phaser

# 启动服务 (映射端口 8000)
docker run -d -p 8000:8000 --name douyin-phaser ghcr.io/yusongxiao/douyin_phaser:main
```

### 方式二：源码部署

#### 1. 安装依赖

需要 Python 3.8+ 环境。

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装浏览器内核 (Playwright 需要)
playwright install chromium
```

#### 2. 启动 API 服务

启动 API 服务器，供其他程序调用：

```bash
python douyin_phaser_api.py
```
服务器默认运行在 `http://localhost:8000`。

**API 使用示例**:

```bash
# 提取视频/图文直链
curl "http://localhost:8000/?url=https://v.douyin.com/abc1234/"
```

**API 文档**:
浏览器访问 `http://localhost:8000/docs` 查看完整的交互式 API 文档。

#### 3. 命令行使用 (CLI)

也可以直接运行脚本提取单个链接：

```bash
python douyin_phaser.py "https://v.douyin.com/abc1234/"
```

## 🛠️ API 返回结构

API 返回统一的 JSON 结构，极大简化了调用方的处理逻辑：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "title": "作品标题",
    "author": "作者昵称",
    "cover": "封面图URL",
    "type": "video | images",  // 顶层类型提示
    "items": [
      // 统一 Items 数组，只需遍历此数组即可
      {
        "type": "video",
        "video_url": "https://...",
        "cover_url": "https://..."
      },
      {
        "type": "image",
        "image_url": "https://..."
      },
      {
        "type": "animated_image",
        "image_url": "https://...webp",
        "video_url": "https://...mp4"
      }
    ]
  }
}
```

## 📚 更多文档

- [技术原理与架构详解](./原理.md)

