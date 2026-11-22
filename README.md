# 汽车维修保修责任采证台

离线的车主授权、零件工序和保修返修采录台。车况诊断与车主签认确定准许施工的工序及费用；零件适配与独立试车决定交车；返厂症状、保修窗口和零件批次决定门店、供应商或车主的责任。三套账各有独立入口和持久化实体，原始 JSON 字节及 SHA256 入库，`audit` 从原始输入重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m autowarranty demo --db demo.sqlite
python -m autowarranty audit --db demo.sqlite
python -m pytest -q
```

CLI `vehicle-import`、`diagnosis-import`、`authorization-import`、`part-import`、`repair-import`、`inspection-import`、`warranty-import`、`return-import` 接受 `file --db state.sqlite`；`warranty-decide --db state.sqlite --case RET1 --at 2026-06-20T00:00:00Z` 追加责任裁定，`part-recall` 处理迟到召回，`report` 查询。正常/失败合成数据见 `fixtures`。真实车主签名、车载诊断设备和供应商质保网络不在本地首版范围。

## 动态排放核证（emission_cert）

维修店为市政车队做尾气动态测试：监管按每次加速段 NOx 质量签道路准入证，修理厂为自家封闭场内低速移车签浓度筛查单。气体分析仪的浓度通道比排气流量计慢几秒，同一时刻的 ppm×流量会把加速前的脉冲错配到加速后的大流量：流量在 t=5s 从 1L/s 升到 10L/s、分析仪在显示 t=6s 报 1 秒宽 100ppm 脉冲时，延迟 0 秒的真实质量是 1000，延迟 2 秒是 100（阈值 500）。常规同刻积分对两种物理历史都输出 1000，固定提前 2 秒又都输出 100——`emission_cert` 不赌事后对齐，而是表达延迟不确定性如何穿过流量台阶进入质量区间：

- **测试链登记**：原始浓度/流量时序与采样频率（`emission_series`，含截顶标记）、分析仪响应核（`analyzer_kernel` 的允许延迟范围）、稀释气流（`dilution_air`，与 run 的排气流量×(稀释倍数−1) 交叉校验）、时钟同步包（`clock_sync` 偏移+漂移）、校准证书（`calibration` 示踪脉冲实测延迟）；动力计负荷在 `emission_run`。
- **反卷积积分**：对可行延迟集合逐一平移对齐后积分，给出峰值、质量区间 `[mass_min, mass_max]` 与阈值裁决；流量台阶、时钟漂移、饱和截顶、测试缺段下不偷偷补零——缺段使质量上界无界，截顶点只作下界。
- **双权限**：`road` 须对整个允许延迟范围证明 `mass_max<500`，否则 `suspended` 并要求示踪校准；`facility` 只看峰值 ≤120ppm。同一测试链可同时持有道路 suspended + 场内 granted（`restriction=closed_facility_only`）；`certify` 不读 repair/inspection，证书不改写车辆状态，场内单不会借 `repair.inspect` 的 `released` 或报表名称变成道路排放证（场内单只锁峰值，不携带道路质量结论）。
- **证书锁定与迟到校准**：证书锁住原件 `source_id` 与 `calibration_id`；迟到的示踪记录触发重估——suspended→granted/denied（`upgraded`/`updated`），granted→其他（`revoked`，如示踪与登记核矛盾 `kernel_contradicted`）；`cert_event` 保留全部变迁，已出具场内单与实际用车事实不被改写。
- **最有辨别力的补测**是示踪脉冲校准：它向尾气管注入已知脉冲、直接实测分析仪延迟，把可行集合塌缩成单点；对既有数据做任何事后对齐都与某个物理历史一致，无法自证。

CLI：`emission-series-import`、`analyzer-kernel-import`、`clock-sync-import`、`dilution-air-import`、`calibration-import` 接受 `file --db`；`emission-certify --db state.sqlite --run ER2 --scope road|facility --at 2026-05-02T09:00:00Z` 出证，`emission-permits --db state.sqlite --vin VIN-1` 汇总两类权限，`emission-readings` 列出测试链。合成时序见 `fixtures/emission-series-*.json`。
