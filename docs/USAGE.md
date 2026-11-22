# 判定环检查器详细使用说明

[← 返回项目总览](../README.md)

本文档说明 `ring_checker.py` 与 `batch_check.py` 的完整功能、参数、判定规则和
输出格式。项目简介与安装方式见根目录 [README.md](../README.md)。

读取一张或多张 Rizline 谱面，遍历所有 Note，在 Note 击打时刻的**绝对坐标**
处寻找可见判定环，输出没有可见判定环的 Note 时间。

判定环可以来自 Note 自己所在的线，也可以来自同一时刻经过同一绝对位置的其他线。

只读取谱面，不修改谱面文件，也不修改前置库 `rizChartFunction`。

## 判定规则

### Note 的绝对位置

- 绝对 X：`rcf.getNotePosition(chart, line, note, isAbsolutePosition=True)`，
  即线节点 `xPosition` 加目标时刻画布的偏移 `xPosition`。
- 谱面 Note 中保存的 `floorPosition` 仍会记录在报告里，作为定位信息。
- 注意：`floorPosition` 是滚动前/谱面坐标下的高度；击打时刻 Note 已经滚到
  判定线，屏幕 Y 为 **0**。不同画布的 `floorPosition` 不能直接用来判断
  两个物体在击打时是否重合。

### 判定环的绝对位置

对每条线、每个候选判定环：

- 绝对 X：`rcf.getLinePosition(chart, line, time, isAbsolutePosition=True)`。
- 击打时刻该线当前点也在判定线上，屏幕 Y 为 **0**。
- 因此跨线判定环覆盖 Note 时，只比较绝对 X，不再比较两个画布的
  `floorPosition`。这正是画布移动带来的位置变化。
- 判定环颜色按时间轴计算：
  - 第一个判定环节点之前，保持第一个节点的 `startColor`；
  - 正好命中节点时，使用同一时间最右侧节点的 `startColor`；
  - 两个节点之间，从当前节点 `startColor` 线性过渡到 `endColor`；
  - 最后一个节点到线结束，继续从 `startColor` 线性过渡到 `endColor`；
  - 线结束之后保持 `endColor`。
- alpha 大于等于 `--min-alpha`（默认 `1`，范围 0-255）即为可见。

### 覆盖判断

- Note 自己所在线的判定环在 Note 时刻可见时，直接认为 Note 有环。
- 其他线需要在 Note 时刻与 Note 的绝对 X 重合：
  - `abs(ring_x - note_x) <= --x-tolerance`
  - 默认 `--x-tolerance` 为 `0.01`，给判定环半径和浮点误差留出余量。
- 击打时刻所有当前判定环都在判定线上，Y 相同，因此不比较
  谱面里不同画布的 `floorPosition`。
- 多条线重叠时，任意一条可见环都算覆盖。
- `--y-tolerance` 现在用于静止线的屏幕 Y 判断（线是否落在判定线上），
  不参与普通判定环的跨线匹配。

### 闪现环

- Note 时刻不可见时，默认向 Note 之后搜索 `0.1` 秒：
  - 搜索窗口内出现可见判定环：记为“闪现环”，默认也算问题；
  - Note 时刻和窗口内都不可见：记为“无判定环”。
- 搜索窗口内检查的是在 Note 时刻与 Note 绝对位置重合的候选线。
- `--accept-flash` 可以把闪现环视为正常。
- `--window 0` 表示只检查 Note 时刻，完全不看前后窗口。
- `--window-before` 可以增加向 Note 之前搜索。

> “闪现环”在这里指：Note 时刻本身没有可见环，但在其前后一小段时间内环短暂可见。

### 静止线（可选，`--stationary-line`）

有些谱师会用引导线模拟“判定线”。启用后，额外把满足以下条件的线视为可见判定环：

- 线在 Note 时刻的屏幕折线穿过 Note 的击打位置（判定线，屏幕 Y=0）；
- 线在 Note 时刻的前一小段时间或后一小段时间内 `floorPosition` 近似不变；
- 线本身颜色 alpha 不小于 `--stationary-min-alpha`。

细节：

- 画布在当前时刻的速度绝对值不超过 `--stationary-speed-tolerance`
  （默认 `0.01`）时，直接视为静止；这覆盖速度设为 `0` 或 `0.001` 的判定线。
- 速度不是严格 0 时，也会在 `--stationary-window`（默认 `0.1` 秒，按 BPM 换算成 tick）
  内检查 floorPosition 前后是否近似不变；任意一侧满足即可。
- 静止线按段检查，可以处理一条线跨多个画布、只有其中一段静止的情况。
- 不要求 `linePoints` 的时间范围包含 Note 时刻：画布速度为 0 时，一个未来/过去
  时间的水平线也可能一直停在判定线上。
- X 覆盖使用整条线在 Note 时刻的折线位置，不只使用线在 Note 时刻的当前点。
  因此一条横线可以从左到右穿过 Note。
- 线的屏幕 X 使用 `xPosition + 画布在 Note 时刻的偏移`。
- 线的屏幕 Y 使用 `floorPosition - 该画布在 Note 时刻的滚动高度`；
  必须接近 0（`--y-tolerance`），即线落在判定线上。
- 默认关闭，避免改变原有“只看判定环”的结果。

### 范围搜索（可选，`--range-search`）

有些谱师会用一条或多条线在 Note 周围画一个类似判定环的图形，线本身不一定穿过
Note。启用范围搜索后：

- 以 Note 的绝对 X 和 `floorPosition` 为中心，构造可配置矩形：
  - X 半宽：`--range-x`，默认 `0.05`
  - floorPosition 半高：`--range-y`，默认 `0.1`
- 对每条线，在 Note 时刻还原它的可见折线：
  - 节点 X：`xPosition + 画布在 Note 时刻的偏移`
  - 节点 Y：`floorPosition`
  - 线节点颜色和 `lineColor` 覆盖色会做透明/背景伪透明过滤
- 按 `--range-samples` 对每个线段采样；
- 如果线至少有一个可见采样点落在矩形内，则计算整条线的可见部分形心；
- 形心与 Note 位置在矩形范围内 → 视为线模拟了判定环；
- 多条候选线的合并形心也在范围内时，同样视为覆盖。

参数：

- `--range-search`：启用；
- `--range-x` / `--range-y`：矩形半宽/半高；
- `--range-samples`：每个线段采样数，默认 `4`；
- `--range-min-samples`：参与形心计算的最小可见采样数，默认 `4`。

### 伪透明（与背景同色）

判定环和静止线在计为可见之前，还会检查“看起来透明”的情况：

- 判定环：
  - 通过 `getRingColor()` 查询 Note 时刻的颜色；
  - alpha 小于 `--min-alpha` 时忽略；
  - RGB 与当前主题背景色相差不超过 `--background-tolerance` 时忽略；
  - 闪现环在窗口内也按颜色检查，不再只看 alpha。
- 静止线：
  - 线节点颜色 alpha 小于 `--stationary-min-alpha` 时忽略；
  - 用线节点颜色和 `lineColor` 覆盖色按 `mixColor` 公式混合；
  - 混合后的 RGB 与当前主题背景色相差不超过 `--background-tolerance` 时忽略。
- 满足任一条件即视为该环/线不可见，不参与覆盖判断。

主题背景色取当前主题 `colorsList[0]`：

- 若 Note 时刻处于某个 Riztime 区间 `[start, end]`，使用该 Riztime 对应主题；
- 多个 Riztime 同时覆盖时，取 `start` 最晚的那个；
- `start` 也相同时，取索引最大的主题；
- 没有 Riztime 覆盖时使用普通主题（主题 0）。

### Hold

Hold 只检查头部时间；Hold 尾部没有终点判定，不在本工具范围内。

## 时间口径

- 输出同时包含 tick 和绝对秒数。
- 秒数从谱面 tick 0 的 BPM 时间轴开始计算，不叠加 `chartDelayMs`。
- 官方谱面（例如 `Chart_IN`）的第一个 BPM 节点可能在 tick 4，库要求 tick 0 有 BPM 节点。
  工具会在内存副本中补一个同倍率的 tick 0 节点用于换算，不会写回文件。
- 如果谱面没有 BPM 节点，会尝试用 `bPM` 作为常量 BPM 换算。

## 运行

脚本会自动尝试导入 `rizChartFunction`：
1. 优先使用已安装的包；
2. 其次使用环境变量 `RIZCHARTFUNCTION_SRC` 指向的 `src` 目录；
3. 再使用同级的 `rizChartFunction_Library/0.2.0/src` 或 `0.1.0/src`；
4. 也兼容本地的 `pytool/0.2.0/src` 或 `pytool/0.1.0/src`。

```powershell
cd path\to\Rizline-Ring_Check_Tool

# 单张谱面
python ring_checker.py "charts\8BLUE\Chart_EZ.json"

# 多张谱面
python ring_checker.py "charts\8BLUE\Chart_EZ.json" "charts\8BLUE\Chart_HD.json"

# 目录：递归查找谱面文件
python ring_checker.py "charts"

# glob
python ring_checker.py "charts\**\*.json"

# 只看 Note 时刻，不做前后搜索
python ring_checker.py chart.json --window 0

# 把窗口内闪现环当作正常
python ring_checker.py chart.json --accept-flash

# 把静止引导线也当作判定环
python ring_checker.py chart.json --stationary-line

# 调整“与背景同色”的容差
python ring_checker.py chart.json --background-tolerance 2

# 搜索 Note 周围线形心模拟的判定环
python ring_checker.py chart.json --range-search `
  --range-x 0.05 --range-y 0.1

# 调整静止线判定
python ring_checker.py chart.json --stationary-line `
  --stationary-window 0.2 `
  --stationary-tolerance 0.002 `
  --stationary-speed-tolerance 0.01 `
  --stationary-min-alpha 1

# 放宽其他线覆盖 Note 的坐标容差
python ring_checker.py chart.json --x-tolerance 0.003 --y-tolerance 0.003

# 只输出问题 Note 的秒数，每行一个
python ring_checker.py chart.json --format times

# JSON / CSV 报告
python ring_checker.py chart.json --format json -o report.json
python ring_checker.py chart.json --format csv -o report.csv

# 有问题时返回退出码 1（适合批处理）
python ring_checker.py chart.json --fail-on-problem
```

## 批量检查

`batch_check.py` 会递归查找 `Chart_*`、`mr_Chart_*`、`challenge/*_Chart`
等谱面文件（带不带 `.json` 后缀均可），并在输出目录中保持与源目录相同的结构：

```powershell
python batch_check.py "charts" "charts-result" `
  --stationary-line --range-search
```

源文件：

```text
charts\8BLUE\Chart_EZ.json
```

对应结果（结果名会去掉 `.json` 后缀）：

```text
charts-result\8BLUE\Chart_EZ-result.txt
```

`_summary.json` / `_summary.csv` 会额外汇总整批检查结果。

## 输出字段

文本格式的每一行：

```text
tick             time(s)      mm:ss.mmm    line   note   type   ring   reason
441.000000       151.200000   02:31.200   900    0      tap    -      missing ring
```

- `tick`：Note 头部时间，单位为 tick；
- `time(s)`：从 tick 0 开始的绝对秒数；
- `mm:ss.mmm`：便于阅读的时间；
- `line`：Note 所在引导线索引；
- `note`：该线内的 Note 索引；
- `type`：`tap` / `drag` / `hold`；
- `ring`：闪现环来源线索引；无来源时为 `-`；
- `reason`：
  - `missing ring`：Note 时刻及搜索窗口内都没有可见判定环；
  - `flash only (after)`：只有 Note 之后的闪现环；
  - `flash only (before)`：只有 Note 之前的闪现环；
  - `flash only (both)`：前后都有闪现环。

JSON/CSV 中还会包含问题 Note 的 `note_x` / `note_y`，便于定位。

## 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--window` / `--window-after` | `0.1` | Note 之后搜索秒数；`0` 表示只检查瞬间 |
| `--window-before` | `0` | Note 之前搜索秒数 |
| `--min-alpha` | `1` | 视为可见的最小 alpha（0-255） |
| `--x-tolerance` | `0.01` | 其他线判定环覆盖 Note 的绝对 X 容差 |
| `--y-tolerance` | `0.01` | 静止线屏幕 Y 与判定线的容差 |
| `--background-tolerance` | `2` | 环/线与主题背景色的 RGB 容差（逐通道） |
| `--stationary-line` | 关闭 | 把穿过 Note 的静止引导线也当作判定环 |
| `--stationary-window` | `0.1` | 检查线高度是否静止的前/后时间窗口（秒） |
| `--stationary-tolerance` | `0.001` | 静止线允许的 floorPosition 变化 |
| `--stationary-speed-tolerance` | `0.01` | 画布速度绝对值不超过该值时直接视为静止 |
| `--stationary-min-alpha` | `1` | 静止线视为可见的最小线点颜色 alpha |
| `--range-search` | 关闭 | 搜索 Note 周围线的形心模拟判定环 |
| `--range-x` | `0.05` | 范围搜索矩形 X 半宽 |
| `--range-y` | `0.1` | 范围搜索矩形 floorPosition 半高 |
| `--range-samples` | `4` | 每个线段的采样数 |
| `--range-min-samples` | `4` | 最小可见采样数 |
| `--accept-flash` | 关闭 | 窗口内闪现环视为正常 |
| `--bpm` / `--base-bpm` | 无 | 传给 `rcf.load()` 的基准 BPM 覆盖值 |
| `--format` | `text` | `text` / `times` / `json` / `csv` |
| `-o` / `--output` | 无 | 输出到 UTF-8 文件 |
| `--fail-on-problem` | 关闭 | 有问题 Note 时退出码为 1 |

退出码：

- `0`：运行完成（即使有问题 Note）；
- `1`：使用了 `--fail-on-problem` 且发现问题；
- `2`：参数错误或所有谱面都读取失败。

## 注意

- 判定环位置使用“线在 Note 时刻的当前点 + 画布绝对 X”，不是静态谱面坐标。
- Note 的 `floorPosition` 会记录在报告中用于定位，但不用于跨线覆盖比较；
  击打时刻它在屏幕上的 Y 是判定线。
- 一个 Note 自己所在线的环可见时直接算有环，因为 Note 本身位于该线上。
- 其他线必须在 Note 击打时刻到达 Note 的绝对 X 才算覆盖。
- 静止线只在 Note 击打时刻检查，不参与闪现环窗口。
- 静止线的可见性使用线节点颜色 alpha；`lineColor` 覆盖色不会改变线点 alpha。
- 如果多条线在空间上接近但不完全重合，可以用 `--x-tolerance` / `--y-tolerance`
  调整覆盖范围。
- 谱面读取使用 `rcf.load()`，会执行库的清洗和规范化；这不是逐字节原文检查。
- 分析前会在内存副本中执行一次 `refresh(mode="all", type="all")`，排序时间数组并重算
  `floorPosition`。有些编辑器样本的 `linePoints` 时间不是非递减，`load()` 不会排序；
  `refresh()` 可修复这类线。输出中的 line/note 索引对应规范化后的谱面，不会修改原文件。
