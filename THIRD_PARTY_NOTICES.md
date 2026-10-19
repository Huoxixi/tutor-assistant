# 第三方技术、数据与许可证

版本来自 2026-10-19 本机已安装发行包元数据，不表示最新版本；完整环境快照见 [requirements-lock.txt](requirements-lock.txt)。源码仓库不捆绑第三方 Python 包，安装时通过包管理器获取。

## 核心组件

| 组件 / 已验证版本 | 用途 | 许可与来源 |
| --- | --- | --- |
| Python 3.13.14 | 语言与标准库 | [PSF](https://docs.python.org/3/license.html) |
| FastAPI 0.143.3 | 本地 API | [MIT](https://github.com/fastapi/fastapi/blob/master/LICENSE) |
| Uvicorn 0.54.0 | ASGI 服务 | [BSD-3-Clause](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) |
| Pydantic 2.14.1 | 数据校验 | [MIT](https://github.com/pydantic/pydantic/blob/main/LICENSE) |
| Starlette 1.8.0 | FastAPI 的 ASGI 基础 | [BSD-3-Clause](https://github.com/Kludex/starlette/blob/main/LICENSE.md) |
| HTTPX 0.28.1 | 可选模型 / 搜索客户端 | [BSD-3-Clause](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| SQLite（标准库使用） | 本地数据存储 | [Public Domain](https://www.sqlite.org/copyright.html) |
| pytest 9.1.1 | 测试 | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |

## Windows 微信读取依赖

| 组件 / 已验证版本 | 用途 | 发行包标注许可与来源 |
| --- | --- | --- |
| pywechat127 1.9.8 | 提供 `pyweixin` 导航模块 | [PyPI 1.9.8：LGPLv3](https://pypi.org/project/pywechat127/1.9.8/)；[上游仓库](https://github.com/Hello-Mr-Crab/pywechat) |
| pywinauto 0.6.9 | Windows UI Automation | [BSD 3-clause](https://github.com/pywinauto/pywinauto/blob/master/LICENSE) |
| pywin32 312 | COM / Win32 / DPAPI 支持 | [元数据为 PSF；组件许可见上游各目录](https://github.com/mhammond/pywin32) |
| comtypes 1.4.17 | COM 互操作 | [MIT](https://github.com/enthought/comtypes/blob/master/LICENSE.txt) |
| psutil 7.2.2 | 适配包的进程支持 | [BSD-3-Clause](https://github.com/giampaolo/psutil/blob/master/LICENSE) |
| PyAutoGUI 0.9.54 | 适配包的桌面操作依赖 | [BSD](https://github.com/asweigart/pyautogui/blob/master/LICENSE.txt) |
| pyperclip 1.11.0 | 适配包的剪贴板依赖 | [BSD](https://github.com/asweigart/pyperclip/blob/master/LICENSE.txt) |

本项目固定安装 `pywechat127==1.9.8`，通过正常 Python 导入使用其 `pyweixin` 模块，不复制第三方包源码到仓库，不修改其安装文件。历史遍历、消息边界、时间解析与本地订单识别位于本项目代码中。

**版本许可区别：** 1.9.8 发行包的 License classifier 为 LGPLv3，License 字段为 LGPL；2026-10-19 核对的[上游主分支 LICENSE](https://github.com/Hello-Mr-Crab/pywechat/blob/main/LICENSE) 则为 LGPL 2.1 文本。这里按固定发行包元数据标注，不将两者混写成同一版本。请保留实际发行物的许可证与来源，不将本项目 MIT 声明当作微信组件的许可证。升级、复制实现或制作捆绑安装包前，重新核对对应版本许可及分发要求。

上述清单涵盖主要组件及部分传递依赖，不替代每个发行物自带的许可文件。Windows UI Automation、COM 和 DPAPI 属于系统能力，微信客户端属于独立第三方产品，均不随本仓库分发。

## 内置地区数据：Vant Area Data

- 来源：[youzan/vant · packages/vant-area-data](https://github.com/youzan/vant/tree/main/packages/vant-area-data)。
- 许可：[MIT](https://github.com/youzan/vant/blob/main/LICENSE)，原文完整保留在 [app/assets/regions-LICENSE.txt](app/assets/regions-LICENSE.txt)。
- 归属：Youzan、Chen Jiahan 及其他贡献者，以所保留许可原文为准。
- 本地处理：`tools/build_regions.py` 将 `app/assets/regions-source.ts` 转为 `app/assets/regions.json`，用于省、市、区县选择及本地匹配。
- 数据记录获取日期为 2026-10-18；JSON 保留来源、许可、日期与源文件哈希。这是项目自带快照，不是实时行政区划服务。
- 本项目未使用 Vant 前端组件库，仅使用上述地区数据。

## 本项目许可证

原创代码采用 [MIT License](LICENSE)，版权所有者为 Huoxixi。第三方代码、数据及权利人声明独立保留；本项目许可证不改变第三方许可。
