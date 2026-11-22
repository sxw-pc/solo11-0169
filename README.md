# 汽车维修保修责任采证台

离线的车主授权、零件工序和保修返修采录台。车况诊断与车主签认确定准许施工的工序及费用；零件适配与独立试车决定交车；返厂症状、保修窗口和零件批次决定门店、供应商或车主的责任。三套账各有独立入口和持久化实体，原始 JSON 字节及 SHA256 入库，`audit` 从原始输入重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m autowarranty demo --db demo.sqlite
python -m autowarranty audit --db demo.sqlite
python -m pytest -q
```

CLI `vehicle-import`、`diagnosis-import`、`authorization-import`、`part-import`、`repair-import`、`inspection-import`、`warranty-import`、`return-import` 接受 `file --db state.sqlite`；`warranty-decide --db state.sqlite --case RET1 --at 2026-06-20T00:00:00Z` 追加责任裁定，`part-recall` 处理迟到召回，`report` 查询。正常/失败合成数据见 `fixtures`。真实车主签名、车载诊断设备和供应商质保网络不在本地首版范围。

## OTA 升级暴露判定与车队随机试验

`ota_trial` 在 OTA 与试验采录之上回答两个不同问题：运维安全准则只检验随机意向组在同等里程下是否出现预设严重告警恶化，合格可把下一批随机扩至扩组池的 20%；供应商绩效准则必须等车端启动证据闭合后按预注册因果估计验收。服务器指派不等于车端启动：`written` 回执只证明镜像写入，暴露时段由随机指派种子、下载/写入/可信启动计数、车端版本证明（`version_attestation` 携带 `booted_at`）、行程里程与道路风险、告警采集覆盖率重建；`written` 不代 `booted`，回执完整子集与意向组均值都不签成合同效果，而是报告回执丢失下的效果界与区分"漏传启动/未启动"的最少车端追证。同车多版本交替、共享危险路段地图、控制组被服务端更新污染都会列入独立性违例并暂缓验收；迟到严重告警或随机化偏离以新决策取代旧决策（旧扩组事实保留），准则可再出版本。

```powershell
python -m autowarranty trial-wave-import fixtures/trial-wave-ok.json --db state.sqlite
python -m autowarranty release-criterion-import fixtures/release-criterion-ops-ok.json --db state.sqlite
python -m autowarranty version-attestation-import fixtures/version-attestation-ok.json --db state.sqlite
python -m autowarranty hazard-map-import fixtures/hazard-map-ok.json --db state.sqlite
python -m autowarranty ota-trial-evaluate --db state.sqlite --criterion OPS --at 2026-06-10T00:00:00Z
python -m autowarranty ota-trial-exposure --db state.sqlite
```
