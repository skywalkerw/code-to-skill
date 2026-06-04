# Apache Fineract Agent Skill

> 版本: 0.2.0
> 适用: Codex / Claude Code 在执行 Fineract 代码审查、Bug 修复、Feature 开发时
> Token 预算: ~1200
> 更新: 2026-06-04

---

## 一、工作流程 (Workflow)

### 1.1 修改会计分录逻辑
1. 确认涉及的 GL Account（总账科目）和 Financial Activity
2. 检查 JournalEntry 类型（DEBIT/CREDIT）是否正确
3. 确认触发条件：贷款发放 / 还款 / 费用扣款 / 利息计提
4. 验证: 借貸平衡（Debits = Credits），金额为正
5. 运行相关集成测试（`integration-tests/` 目录下对应模块）

### 1.2 修改利率计算
1. 确认计息方式: declining balance / flat / 等额本息（equal installment）
2. 检查 `interestRecalculation` 相关配置
3. 确认精度（`BigDecimal` scale）与产品定义一致
4. 更新相关摊销逻辑（`LoanRepaymentScheduleGenerator`）
5. 验证: 还款计划重新计算后，总本金+利息 = 原始金额

### 1.3 添加新费用类型
1. 检查 `Charge` 枚举或配置是否已存在
2. 确认 `isPenalty` 标记是否正确（罚金 / 普通费用）
3. 在 `CashBasedAccountingProcessorForLoan` / `AccrualBased...` 中注册对应的 GL Account 映射
4. 更新 API 文档（`ChargesApiResourceSwagger`）
5. 验证: 费用计算金额不超过产品配置上限

### 1.4 处理支付回调（Interoperation）
1. 读取 `InteropTransferRequestData` 中的 `transferCode`
2. 检查 `transferState` 是否为 `COMMITTED` 之前的状态
3. 确认 `fspFee` 和 `fspCommission` 未超过限额
4. 调用对应的 `CommandHandler` 执行状态变更
5. 验证: JournalEntry 记录完整，互操作双方余额一致

---

## 二、必须遵守的约束 (Constraints)

- **审计日志**: 所有涉及金额或状态的变更，必须写入 `JournalEntry`，不得直接修改数据库账户余额
- **借贷平衡**: 每笔 `JournalEntry` 的 Debits 总额 = Credits 总额，`isBalanced()` 必须返回 true
- **费用授权**: 新增费用类型必须在系统配置中注册（`Charge` 定义 + GL Account 映射）
- **罚金上限**: `penaltyAmount` ≤ 产品定义的 `maxPenaltyLimit`，不允许无上限罚金
- **利息精度**: `BigDecimal` 运算使用产品配置的 `digitsAfterDecimal`，不得随意改变精度
- **事务边界**: 涉及多个 `Repository.save()` 的操作必须在同一事务中，使用 `@Transactional`
- **幂等性**: 支付/回调接口必须支持幂等，`transferCode` 已处理的请求直接返回已有结果

---

## 三、禁止行为 (Do NOT)

- ❌ 不得跨过 `JournalEntryRepository.save()` 直接修改 GL Account 余额
- ❌ 不得新增未在 `AccountingConstants` 中定义的 `FinancialActivity`
- ❌ 不得修改罚金上限（`maxPenaltyLimit`）而不经过产品审批流程
- ❌ 不得在 loan disbursement 中跳过 `validateGLAccountForTransaction()`
- ❌ 不得在 `CommandHandler` 中直接调用 Repository 而不通过 Service 层
- ❌ 不得修改 `interestRecalculation` 逻辑而不更新对应的 `LoanRepaymentScheduleGenerator`
- ❌ 不得使用 `double` 或 `float` 进行金融计算，必须使用 `BigDecimal`

---

## 四、常见失败模式 (Failure Modes)

| 症状 | 根因 | 修复方向 |
|------|------|----------|
| JournalEntry 保存失败 | `isBalanced()=false` | 检查 Debit/Credit 金额是否在创建时正确设置 |
| 还款后利息出现负值 | 预还款时未重新计算利息 | 在 `interestRecalculation` 中处理 prepayment 场景 |
| 费用扣款重复 | 幂等检查缺失 | 在 `chargePayment` 前检查 `transferCode` 是否已存在 |
| GL Account 余额异常 | 直接修改余额绕过 JournalEntry | 所有余额变更必须通过 JournalEntry |
| loan disbursement 丢失 | 事务回滚后未通知外部系统 | 确保 `@Transactional` 范围覆盖完整的 disbursement 流程 |

---

## 五、验证检查清单 (Validation)

- [ ] 所有 GL Account 操作有对应的 JournalEntry
- [ ] `isBalanced()` 返回 true
- [ ] 费用类型在允许列表中
- [ ] 罚金未超过 `maxPenaltyLimit`
- [ ] `BigDecimal` 精度与产品配置一致
- [ ] 互操作请求有 `transferCode` 幂等检查
- [ ] `@Transactional` 覆盖多表操作
- [ ] 集成测试通过（`./gradlew integrationTest`）
