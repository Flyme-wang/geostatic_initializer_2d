# Geostatic Initializer v2 -- 2D/3D Geostatic Stress Initialization

**English** | [中文](#中文说明)

> **v2 New:** 3D model support (C3D8/C3D20...), dual solving methods (TIN Fortran / Pre-compute Direct Assign), auto-detect 2D/3D, Z-axis vertical convention.

Abaqus/CAE plugin that generates initial geostatic stress and pore pressure fields for layered soil/rock models. Detects regions removed by ModelChange and excludes staged deposits.

---

## v2 What's New

| Feature | v1 (2D only) | v2 |
|---------|-------------|-----|
| Element types | CPE*, CAX* | + C3D4/6/8/10/20 (solid & pore) |
| Coordinate convention | X=horizontal, Y=vertical | X,Y=horizontal, **Z=vertical upward** |
| Solving method | Fortran SIGINI | Fortran SIGINI **or** Pre-compute Direct Assign |
| GUI method selector | -- | Radio button: TIN / Pre-compute |
| Auto-detection | -- | Auto-routes 2D/3D based on element types |
| Complex geometry | Columnar mesh only | Pre-compute handles pinch-outs, lenses |

### Solving Methods

**Method 1: TIN Interpolation (Fortran SIGINI)**
- Fast runtime interpolation via Fortran user subroutine
- Generates `initial_fields_generated_3d.for` with TIN barycentric lookup
- Best for columnar/extruded meshes

**Method 2: Pre-compute & Direct Assign (No Fortran)**
- Python pre-computes all element stresses and node pore pressures
- Injects `*INITIAL CONDITIONS, TYPE=STRESS` directly into keyword block
- No Fortran compiler required
- Handles complex geometry (pinch-outs, lenses, irregular layering)
- Stress values visible and auditable in `.inp` file

---

## Install

Copy the complete `geostatic_initializer_2d` directory into an Abaqus plugin directory:

```
C:\Users\<user>\abaqus_plugins\geostatic_initializer_2d
```

Or version-specific:

```
# Abaqus 2024
<install>\EstProducts\2024\win_b64\code\python3.10\lib\abaqus_plugins\

# Abaqus 2021
<install>\EstProducts\2021\win_b64\code\python2.7\lib\abaqus_plugins\

# Abaqus 2026
<install>\EstProducts\2026\win_b64\code\python3.10\lib\abaqus_plugins\
```

Restart Abaqus/CAE. Plugin: `Plug-ins -> Geostatic Initializer v2 (2D/3D)`.

## Use

1. Mesh model, configure first geostatic/deposition step.
2. Open plugin, select method: **TIN** or **Pre-compute**.
3. Run **Inspect model**.
4. Enter job name and output directory.
5. Run **Generate and apply**.
6. Run **Write input and audit** before submission.

### 3D Models

- Z-axis = **vertical upward** (ground = max Z), gravity = -Z
- C3D4, C3D6, C3D8/R, C3D10, C3D15, C3D20/R + pore variants
- One material per instance (use section assignments for multi-material)

## Files

```
geostatic_initializer_2d/
├── geostatic_initializer_core.py               # 2D core
├── geostatic_initializer_core_3d.py            # 3D core: TIN, free-face extraction
├── geostatic_initializer_solver.py             # v2: TIN/Raycast/Precompute methods
├── geostatic_initializer_generator.py          # 2D Fortran generator
├── geostatic_initializer_generator_3d.py       # 3D Fortran generator (TIN)
├── geostatic_initializer_generator_precompute.py  # v2: keyword-block generator
├── geostatic_initializer_kernel.py             # CAE adapter, method router
├── geostatic_initializer_form.py               # AFX command form
├── geostatic_initializer_db.py                 # AFX dialog (method selector)
├── geostatic_initializer_plugin.py             # Plugin registration
└── README.md
```

---

---

## 中文说明

> **v2 新增:** 三维模型支持（C3D8/C3D20 等）、双求解方法选择、2D/3D 自动检测、Z 轴垂直向上惯例。

### v2 新特性

| 功能 | v1（仅二维） | v2 |
|------|------------|-----|
| 单元类型 | CPE*, CAX* | + C3D4/6/8/10/20（实体及孔压版） |
| 坐标约定 | X=水平, Y=垂直 | X,Y=水平面, **Z=垂直向上** |
| 求解方法 | Fortran SIGINI | Fortran SIGINI **或** 预计算直接赋值 |
| 界面选择 | -- | 单选按钮: TIN 插值 / 预计算 |
| 自动检测 | -- | 根据单元类型自动路由 2D/3D |
| 复杂几何 | 仅柱状网格 | 预计算方法支持尖灭、透镜体 |

### 求解方法

**方法一: TIN 插值 (Fortran SIGINI)**
- 通过 Fortran 子程序运行时插值, 速度快
- 生成 `initial_fields_generated_3d.for`
- 适用于柱状/拉伸网格

**方法二: 预计算直接赋值 (无需 Fortran)**
- Python 预计算所有单元应力和节点孔压
- 直接将初始条件注入 inp 关键字块
- 无需 Fortran 编译器
- 可处理复杂几何 (尖灭、透镜体、不规则层理)
- 应力值可在 inp 文件中查看审计

### 三维模型注意

- Z 轴必须**垂直向上** (地面 = 最大 Z), 重力 -Z
- 支持 C3D4/6/8/R/10/15/20/R 及孔压版
- 每个 instance 需单一材料 (多材料用 section assignment 拆分)

### 安装与使用

将 `geostatic_initializer_2d` 目录复制到 Abaqus 插件路径, 重启 CAE。
插件位于 `Plug-ins -> Geostatic Initializer v2 (2D/3D)`。
