# Rizline 判定环检查器

一个用于检查 Rizline 谱面 Note 判定环可见性的 Python 工具。

工具会遍历谱面中的所有 Note（Hold 只检查头部），在 Note 击打时刻寻找可见的
判定环或模拟判定环的线，并输出疑似缺环的 Note 时间、tick、所在线和 Note 索引。

只读取谱面，不修改谱面文件，也不修改前置库 `rizChartFunction`。

## 功能

- 单张谱面、多个文件、目录和 glob 批量检查
- 同线判定环、其他线判定环检查
- 使用包含画布偏移的绝对 X 进行跨线匹配
- 闪现环检测
- 静止引导线模拟判定环
- Note 周围线形心范围搜索
- 透明色、与主题背景同色的伪透明过滤
- Riztime 主题背景切换
- 文本 / 时间列表 / JSON / CSV 输出
- 批量检查并保持源目录结构
- 汇总 `_summary.json` / `_summary.csv`

详细判定规则、全部参数和输出字段见
[详细使用说明](docs/USAGE.md)。

## 前置库

本工具依赖 **rizChartFunction**：

- GitHub：<https://github.com/xp00000002/rizChartFunction_Library.git>
- Python：`>= 3.10`

### 下载并安装

```powershell
git clone https://github.com/xp00000002/rizChartFunction_Library.git
cd rizChartFunction_Library\0.2.0
python -m pip install -e .
```

如果只需要核心谱面功能，不需要额外安装字体依赖。需要字体相关功能时：

```powershell
python -m pip install -e ".[font]"
```

### 导入方式

```python
import rizChartFunction as rcf
```

本工具会按以下顺序查找前置库：

1. 已安装的 `rizChartFunction` 包；
2. 环境变量 `RIZCHARTFUNCTION_SRC` 指向的 `src` 目录；
3. 同级的 `rizChartFunction_Library/0.2.0/src` 或 `rizChartFunction_Library/0.1.0/src`；
4. 同级的 `pytool/0.2.0/src` 或 `pytool/0.1.0/src`。

也可以手动设置：

```powershell
$env:RIZCHARTFUNCTION_SRC = "path\to\rizChartFunction_Library\0.2.0\src"
```

## 快速开始

```powershell
git clone https://github.com/xp00000002/Rizline-Ring_Check_Tool.git
cd Rizline-Ring_Check_Tool

# 单张谱面
python ring_checker.py "charts\8BLUE\Chart_IN.json"

# 推荐：同时检查静止线和范围搜索
python ring_checker.py "charts\8BLUE\Chart_IN.json" --stationary-line --range-search

# 多张 / 目录
python ring_checker.py "charts\8BLUE\Chart_EZ.json" "charts\8BLUE\Chart_HD.json"
python ring_checker.py "charts"

# 只输出问题 Note 的秒数
python ring_checker.py chart.json --format times

# JSON / CSV
python ring_checker.py chart.json --format json -o report.json
python ring_checker.py chart.json --format csv -o report.csv
```

## 批量检查

```powershell
python batch_check.py "charts" "charts-result" --stationary-line --range-search
```

源文件：

```text
charts\8BLUE\Chart_EZ.json
```

对应结果：

```text
charts-result\8BLUE\Chart_EZ-result.txt
```

批量结果还会生成：

```text
charts-result\_summary.json
charts-result\_summary.csv
```

## 常用参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--stationary-line` | 关闭 | 把穿过 Note 的静止引导线视为判定环 |
| `--range-search` | 关闭 | 搜索 Note 周围可见线的形心 |
| `--window` | `0.1` | Note 之后闪现环搜索秒数 |
| `--min-alpha` | `1` | 判定环可见的最小 alpha |
| `--background-tolerance` | `2` | 与主题背景色的 RGB 容差 |
| `--x-tolerance` | `0.01` | 跨线判定环的绝对 X 容差 |
| `--format` | `text` | `text` / `times` / `json` / `csv` |
| `--fail-on-problem` | 关闭 | 有问题 Note 时返回退出码 1 |

完整参数见 [docs/USAGE.md](docs/USAGE.md) 或：

```powershell
python ring_checker.py --help
python batch_check.py --help
```

## 仓库结构

```text
Rizline-Ring_Check_Tool/
├── README.md                  # 项目总览
├── ring_checker.py            # 单张 / 多张谱面检查器
├── batch_check.py             # 批量检查并镜像输出目录
└── docs/
    └── USAGE.md               # 详细使用说明与判定规则
```

测试谱面、测试输出和检查结果不会提交到仓库。

## 说明

- 检查结果用于辅助排查谱面判定环问题，不代表官方判定。
- 判定环颜色、主题背景色和线色覆盖均通过前置库查询或按前置库规则计算。
- `--stationary-line`、`--range-search` 是启发式检查，可按谱面风格调整容差和窗口。
- 项目不会修改输入的谱面文件。
