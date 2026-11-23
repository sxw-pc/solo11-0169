# 汽车维修保修责任采证台

离线的车主授权、零件工序和保修返修采录台。车况诊断与车主签认确定准许施工的工序及费用；零件适配与独立试车决定交车；返厂症状、保修窗口和零件批次决定门店、供应商或车主的责任。三套账各有独立入口和持久化实体，原始 JSON 字节及 SHA256 入库，`audit` 从原始输入重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m autowarranty demo --db demo.sqlite
python -m autowarranty audit --db demo.sqlite
python -m pytest -q
```

CLI `vehicle-import`、`diagnosis-import`、`authorization-import`、`part-import`、`repair-import`、`inspection-import`、`warranty-import`、`return-import` 接受 `file --db state.sqlite`；`warranty-decide --db state.sqlite --case RET1 --at 2026-06-20T00:00:00Z` 追加责任裁定，`part-recall` 处理迟到召回，`report` 查询。正常/失败合成数据见 `fixtures`。真实车主签名、车载诊断设备和供应商质保网络不在本地首版范围。

## 跨照明条件的补漆配色

`paint_match` 在采证之上做配方求解与库存锁定：原车漆、试喷板、颜料批次的光谱挂在统一波段坐标（默认几何 `45/0`、校准 `CAL1`）下，未测波段按区间族传播不确定性；涂层色由 Kubelka-Munk 混合律（底色漆、膜厚、清漆）计算，颜料反射率绝不直接线性相加。日间车队订单只要求 D65，已试喷且库存足够的配方直接生成可执行调漆单并锁定批次；OEM 多光源订单要求 D65 与窄带路灯同时匹配，缺波段时先生成补测/试喷工单并保留物料，区分候选所需的最少波段/角度由 `paint-candidates` 给出。迟到的路灯测量推翻已喷配方时，领料与实喷事实保留，仅生成返工与库存冲销。

新增 CLI：`paint-order-import`、`band-measure-import`、`paint-plan`、`paint-draw`、`paint-spray-import`、`paint-rework-import` 接受 `file --db`；`paint-stock`、`paint-order-status --order`、`paint-candidates --order` 查询。合成数据覆盖：同 D65/四波段但夜间不同的两种涂层、两种订单路线、颜料批次竞争与已喷后返工，见 `fixtures/paint-order-*.json`、`band-measure-*.json` 与 `tests/test_paint_match.py`。
