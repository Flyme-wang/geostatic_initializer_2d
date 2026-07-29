# Geostatic Initializer 2D

**English** | [中文](#中文说明)

This Abaqus/CAE plugin generates a project-local
`generate_geostatic_initial_fields.py` and `initial_fields_generated.for` pair.
It detects regions removed in the first analysis step and excludes those staged
deposits from initial stress and pore pressure. This is required for
`Model-3Layer-Screenshot-22000`: only `Part-Gray` is initialized; `Part-Purple`
and `Part-LightRed` enter later without inherited initial stress or pressure.

## Install

Copy the complete `geostatic_initializer_2d` directory into an Abaqus plugin
search directory, for example:

```text
C:\Users\<user>\abaqus_plugins\geostatic_initializer_2d
```

Or copy into the version-specific plugin path:

```text
# Abaqus 2024
<install>\EstProducts\2024\win_b64\code\python3.10\lib\abaqus_plugins\

# Abaqus 2021
<install>\EstProducts\2021\win_b64\code\python2.7\lib\abaqus_plugins\

# Abaqus 2026
<install>\EstProducts\2026\win_b64\code\python3.10\lib\abaqus_plugins\
```

Restart Abaqus/CAE. The command appears at
`Plug-ins -> Geostatic Initializer 2D` in the Property, Load, Mesh, and Job
modules.

## Use

1. Mesh the 2D layered model and configure its first geostatic/deposition step.
2. Open the plugin and run **Inspect model**.
3. Enter an existing job name and a project output directory.
4. Run **Generate and apply**. The plugin creates active assembly sets, injects
   one guarded `*INITIAL CONDITIONS, TYPE=STRESS, USER` block and, for coupled
   models, one pore-pressure USER block, then binds the generated `.for` to the
   job. It does not submit the job.
5. Run **Write input and audit** before submission.

The project-local Python entry can be run later inside Abaqus/CAE to regenerate
the sibling Fortran file after geometry, mesh, or material changes.

## Current limits

- Two-dimensional planar or axisymmetric layered continuum meshes only.
- Multiple section/material assignments in one instance are split by local
  element label and mapped in `SIGINI` using `GETPARTINFO`.
- Constant density, void ratio, saturation, fluid specific weight, and K0.
- Supported linear pore elements are listed in `geostatic_initializer_core.py`.
- Profiles are extracted from element free-boundary edges, so a valid mapped
  CPE4/CPE4P mesh may use different x stations on its upper, lower, and internal
  rows. Each material region must still have exactly two continuous,
  single-valued, non-crossing upper/lower boundary chains; holes, branches, and
  non-single-valued boundaries are blocked instead of guessed.
- Initial head follows the active local ground surface. Perched or disconnected
  aquifers are not supported.
- Every porous region must have readable, active void-ratio and saturation
  fields; missing or ambiguous coverage blocks generation instead of defaulting.
- When one instance contains multiple porous section regions with independent
  void-ratio or saturation fields, the current kernel cannot yet disambiguate
  those fields by section-node coverage and blocks generation explicitly.
- Abaqus/CAE does not expose USER-defined `SIGINI` stress through its initial
  condition API. The MVP therefore injects a marker-guarded model-data keyword
  block and uses `keywordBlock` again for input auditing.
- The kernel API calls that create union assembly sets and edit `keywordBlock`
  require a live Abaqus/CAE validation pass before production analysis.

---

## 中文说明

**[English](#geostatic-initializer-2d)** | 中文

本插件为 Abaqus/CAE 插件，用于自动生成二维分层模型的自重初始应力场和孔隙水压力场。
插件会在项目目录下生成 `generate_geostatic_initial_fields.py` 和
`initial_fields_generated.for` 文件对。它能自动检测第一个分析步中被"杀死"
（model change remove）的区域，并将这些分期填筑体排除在初始应力和孔压之外。

### 安装

将完整的 `geostatic_initializer_2d` 文件夹复制到 Abaqus 插件搜索目录，例如：

```text
C:\Users\<用户名>\abaqus_plugins\geostatic_initializer_2d
```

或复制到各版本专属插件路径：

```text
# Abaqus 2024
<安装目录>\EstProducts\2024\win_b64\code\python3.10\lib\abaqus_plugins\

# Abaqus 2021
<安装目录>\EstProducts\2021\win_b64\code\python2.7\lib\abaqus_plugins\

# Abaqus 2026
<安装目录>\EstProducts\2026\win_b64\code\python3.10\lib\abaqus_plugins\
```

重启 Abaqus/CAE 后，在 Property、Load、Mesh、Job 模块中可通过
`Plug-ins -> Geostatic Initializer 2D` 访问。

### 使用方法

1. 完成二维分层模型网格划分，并配置好第一个 Geostatic/Deposition 分析步。
2. 打开插件，点击 **Inspect model**（检查模型）。
3. 输入已有的 Job 名称和项目输出目录。
4. 点击 **Generate and apply**（生成并应用）。插件将自动创建装配集、
   注入带标记保护的 `*INITIAL CONDITIONS, TYPE=STRESS, USER` 关键字块，
   对于耦合模型还会注入孔压 USER 块，并将生成的 `.for` 文件绑定到 Job。
   插件不会自动提交计算。
5. 提交前点击 **Write input and audit**（写入输入文件并审计）进行检查。

项目目录下的 Python 入口文件可在后续修改几何、网格或材料后，
在 Abaqus/CAE 中重新运行以更新 Fortran 文件。

### 当前限制

- 仅支持二维平面应变或轴对称分层连续体网格。
- 同一实例中多截面/材料分配按局部单元标签拆分，通过 `GETPARTINFO` 在
  `SIGINI` 中映射。
- 密度、孔隙比、饱和度、流体容重和 K0 为常量。
- 支持的线性孔压单元列表见 `geostatic_initializer_core.py`。
- 剖面从单元自由边界提取，每个材料区域必须具有恰好两条连续、单值、
  不相交的上下边界链；孔洞、分支和非单值边界会被阻止而非猜测。
- 初始水头沿活动局部地表分布，不支持悬挂含水层或隔断含水层。
- 每个多孔区域必须具有可读的、活动的孔隙比和饱和度场；
  缺失或模糊的覆盖会阻止生成而非使用默认值。
- Abaqus/CAE 不通过初始条件 API 暴露 USER 定义的 `SIGINI` 应力，
  因此本插件通过注入带标记保护的关键字块实现。
- 创建联合装配集和编辑 `keywordBlock` 的内核 API 调用
  需要在正式分析前通过 Abaqus/CAE 验证。

## License / 许可证

MIT
