# 汽车维修保修责任采证台

离线的车主授权、零件工序和保修返修采录台。车况诊断与车主签认确定准许施工的工序及费用；零件适配与独立试车决定交车；返厂症状、保修窗口和零件批次决定门店、供应商或车主的责任。三套账各有独立入口和持久化实体，原始 JSON 字节及 SHA256 入库，`audit` 从原始输入重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m autowarranty demo --db demo.sqlite
python -m autowarranty audit --db demo.sqlite
python -m pytest -q
```

CLI `vehicle-import`、`diagnosis-import`、`authorization-import`、`part-import`、`repair-import`、`inspection-import`、`warranty-import`、`return-import` 接受 `file --db state.sqlite`；`warranty-decide --db state.sqlite --case RET1 --at 2026-06-20T00:00:00Z` 追加责任裁定，`part-recall` 处理迟到召回，`report` 查询。正常/失败合成数据见 `fixtures`。真实车主签名、车载诊断设备和供应商质保网络不在本地首版范围。

## 退役电池梯次利用定级

`battery.py` 只保存原件：电池包关联 VIN 与标称 Ah，充电测试持久化 BMS `start_soc`/`end_soc`、实测 `amp_hours` 与温度，电阻测试录入同包毫欧值；`battery-tests` 只返回这些原件。`repair.py` 的试车交车不读取充电测试、不签电池二次用途，因此车辆已交付、原件齐备时也没有任何用途证书。

`battery_grade.py` 在原件之上建第二套账：包组成与校准版本（`battery-composition-import`，重组即换代，前包测试不得拼接）、已签收货约定（`use-agreement-import`，固定储能站限窗 / 移动电源厂全程最弱单体门槛）、窗口对齐证据（`window-evidence-import`，双独立电量计冲突或 SOC 轨迹漂移即隔离本次测试，不取有利值）、受控完整循环（`full-cycle-import`，缺口循环与未放到截止电压均标记为不可认定全程）。`battery-grade --pack BAT1` 给出已证窗口、仍可能的全程容量集合（拒绝 38Ah/40% 的线性外推）、各约定资格与真正区分候选曲线的补测窗口；`certificate-import` 只按已证范围签发限窗证或全程证，签发时间不得早于约定与证据；`shipment-import` 凭证书装运并生成限制标签；`battery-grade-decide` 在迟到全程试验证明不达标时撤销等待中的高功率订单，已完成的限窗交付与全部时间线保留在 `battery-certificates`。
