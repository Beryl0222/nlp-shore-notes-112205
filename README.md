# 净滩记述中

柳涧两岸的志愿巡查台账全为合成资料。每个季节允许有几场实际巡查。质重单位是千克，件数只能在同类型之间比较。

## 资料格式

数据位于 `data/records.json`，以 UTF-8 保存。各表的记录以本表内的 `id` 或 `seq` 识别。原话保留提交时的文字。

现场常把相邻两段叫作一段，以河岸示意登记的方向与边界为标准。segments 的 from_m 到 to_m 是本岸朝下游的米数，左闭右开。visit effort_minutes 只是组织时长，有记录才能分给各段。规则记录本轮有效比较至少需要的重叠范围和努力条件。正文中的数字是作者的观察或转述，不是既定统计。

- `segments` 保存示意图的实体登记。 字段为 `id`、`bank`、`from_m`、`to_m`、`aliases`。
- `visits` 保存履行巡查时的基本登记。 字段为 `id`、`season`、`at`、`team`、`bank`、`effort_minutes`、`weather`。
- `notes` 保存来自参与者的巡查叙述。 字段为 `id`、`visit`、`writer`、`text`。
- `rounds` 保存季节比较的公开口径及补测时限。 字段为 `id`、`seasons`、`metric`、`allowed_weather`、`effort`、`min_overlap`。
- `bulletins` 保存已给社区的旧报告栏目，part 指主表中相关一级栏目。 字段为 `id`、`part`、`articles`、`citations`、`text`。

## 查看资料与自检

使用 Python 3.11.2 自带的标准库读取资料并验证字段和引用。

```sh
python3 -m json.tool data/records.json
python3 -m unittest discover -s tests -v
```
