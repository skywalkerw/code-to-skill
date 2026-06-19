# Fineract 会计凭证 (Journal Entry) 代码分析

> 分析日期: 2026-06-17
> 代码版本: Apache Fineract (master/develop)
> 源码路径: `demo-project/sources/repos/fineract/`

---

## 一、模块概览

Fineract 的会计系统由 **3 个子模块** 组成，会计凭证相关代码分布在：

| 模块 | 路径 | 说明 |
|------|------|------|
| `fineract-core` | `fineract-core/src/main/java/.../accounting/` | 核心枚举、常量定义 |
| `fineract-accounting` | `fineract-accounting/src/main/java/.../accounting/` | 会计领域模型、API、序列化 |
| `fineract-provider` | `fineract-provider/src/main/java/.../accounting/` | 业务处理器、服务实现 |

**关键包路径：**
```
org.apache.fineract.accounting
├── common/          # 会计常量 (AccountingConstants)
├── journalentry/    # 会计凭证（分录）核心
│   ├── api/         # REST API 参数
│   ├── command/     # 命令对象
│   ├── data/        # DTO / 数据传输
│   ├── domain/      # JPA 实体
│   ├── exception/   # 异常类
│   ├── serialization/  # JSON 序列化
│   └── service/     # 业务服务（核型处理器）
├── rule/            # 会计规则
├── glaccount/       # 总账科目
├── closure/         # 会计期间关闭
├── producttoaccountmapping/  # 产品→科目映射
├── financialactivityaccount/  # 财务活动账户
└── provisioning/    # 准备金
```

---

## 二、核心领域模型

### 2.1 JournalEntry（会计分录实体）

**文件：** `fineract-accounting/.../domain/JournalEntry.java`
**数据库表：** `acc_gl_journal_entry`

```
┌──────────────────────────────────────────────────────────┐
│                   acc_gl_journal_entry                    │
├────────────────────┬─────────────────────────────────────┤
│ id (PK)            │ 主键                                │
│ office_id (FK)     │ 所属营业厅                          │
│ account_id (FK)    │ 总账科目 (GLAccount)                │
│ payment_details_id │ 支付明细                            │
│ reversal_id (FK)   │ 冲销关联（自引用）                   │
│ currency_code      │ 货币代码 (3位)                       │
│ transaction_id     │ 业务交易 ID                          │
│ type_enum          │ 凭证类型: 1=CREDIT(贷), 2=DEBIT(借)  │
│ amount             │ 金额 (BigDecimal, 19位精度6)         │
│ entry_date         │ 入账日期                             │
│ description        │ 描述 (500字符)                       │
│ entity_type_enum   │ 关联实体类型 (Loan/Savings/Client等) │
│ entity_id          │ 关联实体 ID                          │
│ ref_num            │ 参考号                               │
│ reversed           │ 是否已冲销                           │
│ manual_entry       │ 是否手工录入                         │
│ submitted_on_date  │ 提交日期                             │
│ loan_transaction_id   │ 贷款交易 ID (维度)               │
│ savings_transaction_id│ 储蓄交易 ID (维度)               │
│ client_transaction_id │ 客户交易 ID (维度)               │
│ share_transaction_id  │ 股份交易 ID (维度)               │
└────────────────────┴─────────────────────────────────────┘
```

**关键工厂方法：**
```java
JournalEntry.createNew(office, paymentDetail, glAccount, currencyCode,
    transactionId, manualEntry, transactionDate, journalEntryType,
    amount, description, entityType, entityId, referenceNumber,
    loanTransaction, savingsTransaction, clientTransaction, shareTransaction)
```

**借贷判断方法：**
- `isDebitEntry()` → `type_enum == 2`
- `isCreditEntry()` → `type_enum == 1`

### 2.2 JournalEntryType（凭证类型枚举）

**文件：** `fineract-core/.../domain/JournalEntryType.java`

```java
public enum JournalEntryType {
    CREDIT(1, "journalEntryType.credit"),  // 贷方
    DEBIT(2, "journalEntrytType.debit");   // 借方
}
```

**核心方法：**
- `fromInt(v)`: 1→CREDIT, 2→DEBIT
- `isDebitType()`: 判断是否为借方
- `isCreditType()`: 判断是否为贷方

### 2.3 JournalEntryData（DTO）

**文件：** `fineract-accounting/.../data/JournalEntryData.java`

核心字段：
- `id`, `officeId`, `officeName`
- `glAccountId`, `glAccountName`, `glAccountCode`, `glAccountType`
- `entryType` (EnumOptionData: CREDIT/DEBIT)
- `amount` (BigDecimal)
- `transactionId`, `transactionDate`
- `currency`, `currencyCode`
- `entityType`, `entityId`
- `reversed`, `manualEntry`
- `referenceNumber`
- `officeRunningBalance`, `organizationRunningBalance`
- `submittedOnDate`, `createdDate`, `createdByUserName`
- **导入专用字段：** `credits` (List\<CreditDebit\>), `debits` (List\<CreditDebit\>)

### 2.4 CreditDebit（借贷明细）

**文件：** `fineract-accounting/.../data/CreditDebit.java`

```java
public class CreditDebit {
    private final Long glAccountId;   // GL 科目 ID
    private final BigDecimal amount;  // 金额
}
```

这是一个简单的 DTO，用于批量导入时承载借贷方信息。

### 2.5 JournalEntryCommand（创建命令）

**文件：** `fineract-accounting/.../command/JournalEntryCommand.java`

```java
public class JournalEntryCommand {
    private final Long officeId;
    private final String currencyCode;
    private final LocalDate transactionDate;
    private final String referenceNumber;
    private final Long accountingRuleId;         // 会计规则 ID
    private final BigDecimal amount;
    private final Long paymentTypeId;
    private final String accountNumber;
    private final String checkNumber;
    private final String receiptNumber;
    private final String bankNumber;
    private final String routingCode;
    private final SingleDebitOrCreditEntryCommand[] credits;  // 贷方数组
    private final SingleDebitOrCreditEntryCommand[] debits;   // 借方数组
}
```

**校验规则 (`validateForCreate()`):**
1. `transactionDate` 不能为空
2. `officeId` 不能为空且 > 0
3. `currencyCode` 不能为空
4. `comments` ≤ 500 字符
5. `referenceNumber` ≤ 100 字符
6. `credits[]` / `debits[]` 至少一方非空，每项必须提供 `glAccountId` (>0) 和 `amount` (≥0)
7. **借贷金额必须相等** (通过 `validateBusinessRulesForJournalEntries`)

### 2.6 SingleDebitOrCreditEntryCommand

```java
public class SingleDebitOrCreditEntryCommand {
    private final Long glAccountId;
    private final BigDecimal amount;
    private final String comments;
    private final Map<String, Object> additionalParams;
}
```

---

## 三、会计常量体系

**文件：** `fineract-core/.../common/AccountingConstants.java`

### 3.1 贷款产品会计科目占位符

#### 现金制 (CashAccountsForLoan) — 26 个科目

| 枚举值 | 含义 | 典型用途 |
|--------|------|----------|
| FUND_SOURCE(1) | 资金来源 | 放款时借记 |
| LOAN_PORTFOLIO(2) | 贷款组合 | 放款时贷记 |
| INTEREST_ON_LOANS(3) | 贷款利息收入 | 还息时贷记 |
| INCOME_FROM_FEES(4) | 费用收入 | 收费时贷记 |
| INCOME_FROM_PENALTIES(5) | 罚金收入 | 罚金时贷记 |
| LOSSES_WRITTEN_OFF(6) | 核销损失 | 核销时借记 |
| INTEREST_RECEIVABLE(7) | 应收利息 | 权责发生制用 |
| FEES_RECEIVABLE(8) | 应收费用 | 权责发生制用 |
| PENALTIES_RECEIVABLE(9) | 应收罚金 | 权责发生制用 |
| TRANSFERS_SUSPENSE(10) | 转账暂记 | 内部转账 |
| OVERPAYMENT(11) | 超额还款 | 还款超额时贷记 |
| GOODWILL_CREDIT(13) | 商誉贷记 | 核销恢复 |
| CHARGE_OFF_EXPENSE(16) | 核销费用 | 核销借记 |
| CHARGE_OFF_FRAUD_EXPENSE(17) | 欺诈核销费用 | 欺诈处理 |
| INCOME_FROM_RECOVERY(12) | 回收收入 | 坏账回收 |
| INCOME_FROM_CHARGE_OFF_INTEREST(14) | 核销利息回收 | |
| INCOME_FROM_CHARGE_OFF_FEES(15) | 核销费用回收 | |
| INCOME_FROM_CHARGE_OFF_PENALTY(18) | 核销罚金回收 | |
| INCOME_FROM_GOODWILL_CREDIT_INTEREST(19) | 商誉贷记利息 | |
| INCOME_FROM_GOODWILL_CREDIT_FEES(20) | 商誉贷记费用 | |
| INCOME_FROM_GOODWILL_CREDIT_PENALTY(21) | 商誉贷记罚金 | |
| CLASSIFICATION_INCOME(22) | 分类收入 | |
| DEFERRED_INCOME_LIABILITY(23) | 递延收入负债 | |
| INCOME_FROM_DISCOUNT_FEE(24) | 折扣费用收入 | |
| FEES_RECEIVABLE(25) | 应收费用 | 贴现/预扣 |
| PENALTIES_RECEIVABLE(26) | 应收罚金 | 贴现/预扣 |

#### 权责发生制 (AccrualAccountsForLoan) — 25 个科目

在现金制基础上额外增加：
- `INTEREST_RECEIVABLE(7)`: 应收利息
- `FEES_RECEIVABLE(8)`: 应收费用
- `PENALTIES_RECEIVABLE(9)`: 应收罚金
- `INCOME_FROM_CAPITALIZATION(22)`: 资本化收入
- `BUY_DOWN_EXPENSE(24)`: 贴现费用
- `INCOME_FROM_BUY_DOWN(25)`: 贴现收入

### 3.2 储蓄产品会计科目

- **现金制：** `CashAccountsForSavings` — 14 个（SAVINGS_REFERENCE, SAVINGS_CONTROL, INTEREST_ON_SAVINGS 等）
- **权责发生制：** `AccrualAccountsForSavings` — 17 个（增加 INTEREST_PAYABLE, INTEREST_RECEIVABLE 等）

### 3.3 股份产品会计科目

`CashAccountsForShares` — 4 个（SHARES_REFERENCE, SHARES_SUSPENSE, INCOME_FROM_FEES, SHARES_EQUITY）

### 3.4 FinancialActivity 枚举

```java
public enum FinancialActivity {
    ASSET_TRANSFER(100, "assetTransfer", ASSET),
    LIABILITY_TRANSFER(200, "liabilityTransfer", LIABILITY),
    CASH_AT_MAINVAULT(101, "cashAtMainVault", ASSET),
    CASH_AT_TELLER(102, "cashAtTeller", ASSET),
    OPENING_BALANCES_TRANSFER_CONTRA(300, "openingBalancesTransferContra", EQUITY),
    ASSET_FUND_SOURCE(103, "fundSource", ASSET),
    PAYABLE_DIVIDENDS(201, "payableDividends", LIABILITY),
}
```

### 3.5 GLAccountType（总账科目类型）

```java
public enum GLAccountType {
    ASSET(1),       // 资产
    LIABILITY(2),   // 负债
    EQUITY(3),      // 权益
    INCOME(4),      // 收入
    EXPENSE(5);     // 费用
}
```

---

## 四、会计规则 (AccountingRule)

### 4.1 AccountingRule 实体

**文件：** `fineract-accounting/.../rule/domain/AccountingRule.java`
**数据库表：** `acc_accounting_rule`

```java
@Entity
@Table(name = "acc_accounting_rule")
public class AccountingRule {
    private Long id;
    private String name;                    // 规则名称 (unique)
    private Office office;                  // 适用范围 (null=全局)
    private GLAccount accountToDebit;       // 借方总账科目 (FK)
    private GLAccount accountToCredit;      // 贷方总账科目 (FK)
    private String description;             // 描述
    private Boolean systemDefined;          // 是否系统预置
    private boolean allowMultipleCreditEntries;  // 允许多贷方
    private boolean allowMultipleDebitEntries;   // 允许多借方
    private List<AccountingTagRule> accountingTagRules;  // 标签规则
}
```

**核心方法：**
- `fromJson(office, debitAccount, creditAccount, command)`: 从 JSON 创建规则
- `update(command)`: 更新规则属性

### 4.2 会计规则的借贷逻辑

AccountingRule 直接将一个借方科目 (`accountToDebit`) 和一个贷方科目 (`accountToCredit`) 绑定，用于手工分录场景。系统自动生成的分录则通过 `ProductToGLAccountMapping` (产品到科目映射) 决定借贷科目。

---

## 五、JournalEntryWritePlatformService（写服务）

**接口：** `JournalEntryWritePlatformService.java`
**实现：** `JournalEntryWritePlatformServiceJpaRepositoryImpl.java`

### 5.1 核心方法

```java
public interface JournalEntryWritePlatformService {
    // 手工创建分录
    CommandProcessingResult createJournalEntry(JsonCommand command);
    // 冲销分录
    CommandProcessingResult revertJournalEntry(JsonCommand command);
    // 期初余额导入
    CommandProcessingResult defineOpeningBalance(JsonCommand command);

    // 为贷款生成分录（批量）
    void createJournalEntriesForLoan(AccountingBridgeDataDTO dto);
    // 为存款生成分录（批量）
    void createJournalEntriesForSavings(Map<String, Object> data);
    // 为客户交易生成分录（批量）
    void createJournalEntriesForClientTransactions(Map<String, Object> data);
    // 为股份生成分录（批量）
    void createJournalEntriesForShares(Map<String, Object> data);

    // 单笔贷款交易分录
    void createJournalEntriesForLoanTransaction(
        LoanTransaction loanTransaction,
        boolean isAccountTransfer,
        boolean isLoanToLoanTransfer
    );
    // 冲销分录（贷款）
    void createJournalEntryForReversedLoanTransaction(
        LocalDate date, String loanTransactionId, Long officeId
    );

    // 准备金分录
    String createProvisioningJournalEntries(ProvisioningEntry entry);
    String revertProvisioningJournalEntries(LocalDate date, Long entityId, Integer entityType);

    // 外部资产转让分录
    void createJournalEntriesForExternalOwnerTransfer(
        Loan loan, ExternalAssetOwnerTransfer transfer, ExternalAssetOwner previousOwner
    );

    // 股份冲销分录
    void revertShareAccountJournalEntries(ArrayList<Long> transactionId, LocalDate date);
}
```

### 5.2 手工创建分录流程 (`createJournalEntry`)

```
1. 从 API JSON 反序列化 → JournalEntryCommand
2. validateForCreate() — 校验必填字段 + 借贷金额
3. 校验营业厅 + 货币 + 会计规则
4. validateBusinessRulesForJournalEntries()
   └── 校验: 同一 GL 科目不能同时出现在借方和贷方
   └── 校验: 借方总额 == 贷方总额（借贷平衡）
5. 创建 PaymentDetail
6. 生成 transactionId
7. 遍历 debits[] → JournalEntry.createNew(DEBIT)
8. 遍历 credits[] → JournalEntry.createNew(CREDIT)
9. 批量保存 → glJournalEntryRepository.saveAll()
```

---

## 六、会计处理器 (AccountingProcessor) 体系

### 6.1 工厂模式

```java
// 贷款会计处理器工厂
interface AccountingProcessorForLoanFactory {
    AccountingProcessorForLoan determineProcessor(
        LoanDTO loanDTO, List<LoanTransactionDTO> loanTransactionDTOs
    );
}

// 储蓄会计处理器工厂
interface AccountingProcessorForSavingsFactory {
    AccountingProcessorForSavings determineProcessor(
        Map<String, Object> accountingBridgeData
    );
}
```

### 6.2 贷款处理器

| 处理器 | 制式 | 说明 |
|--------|------|------|
| `AccrualBasedAccountingProcessorForLoan` | 权责发生制 | 默认，支持应计/摊销 |
| `CashBasedAccountingProcessorForLoan` | 现金制 | 实收实付 |
| `CashBasedAccountingProcessorForWorkingCapitalLoan` | 现金制 | 营运资金贷款专用 |
| `CashBasedAccountingProcessorForClientTransactions` | 现金制 | 客户交易 |

### 6.3 AccrualBasedAccountingProcessorForLoan（权责发生制核心处理器）

**文件：** `AccrualBasedAccountingProcessorForLoan.java` (~2200 行)

**处理的事务类型：**

| 交易类型 | 借方科目 | 贷方科目 |
|----------|----------|----------|
| **放款 (Disbursement)** | FUND_SOURCE | LOAN_PORTFOLIO |
| **应计 (Accrual)** | INTEREST_RECEIVABLE / FEES_RECEIVABLE / PENALTIES_RECEIVABLE | INTEREST_ON_LOANS / INCOME_FROM_FEES / INCOME_FROM_PENALTIES |
| **还款 (Repayment)** | FUND_SOURCE (按支付渠道) | LOAN_PORTFOLIO (本金部分) + INTEREST_RECEIVABLE (利息部分) + FEES_RECEIVABLE / PENALTIES_RECEIVABLE |
| **核销 (Write-off)** | LOSSES_WRITTEN_OFF | LOAN_PORTFOLIO + INTEREST_RECEIVABLE + FEES_RECEIVABLE + PENALTIES_RECEIVABLE |
| **核销恢复 (Recovery)** | LOAN_PORTFOLIO | INCOME_FROM_RECOVERY + GOODWILL_CREDIT |
| **超额还款 (Overpayment)** | FUND_SOURCE | OVERPAYMENT (负债科目) |
| **退款 (Refund)** | OVERPAYMENT | FUND_SOURCE |
| **转账 (Account Transfer)** | LOAN_PORTFOLIO (源) | LOAN_PORTFOLIO (目标) |

### 6.4 AccountingProcessorHelper（辅助类）

**文件：** `AccountingProcessorHelper.java` (~1300 行)

**核心方法：**

```java
// 创建贷方分录 - 按科目映射
public void createCreditJournalEntryForLoan(Office, currencyCode, accountMappingTypeId,
    loanProductId, loanId, transactionId, date, amount, paymentTypeId, ...)

// 创建借方分录 - 按科目映射
public void createDebitJournalEntryForLoan(Office, currencyCode, accountMappingTypeId,
    loanProductId, loanId, transactionId, date, amount, paymentTypeId, ...)

// 创建贷方分录 - 按科目 ID (用于 Tax)
public void createCreditJournalEntryForLoanByGLAccountId(Office, currencyCode, loanId,
    transactionId, date, amount, glAccountId)

// 创建借方分录 - 按科目 ID
public void createDebitJournalEntryForLoanByGLAccountId(Office, currencyCode, loanId,
    transactionId, date, amount, glAccountId)

// 批量创建借贷分录
public void createJournalEntriesForLoan(Office, currencyCode, debitAccountType,
    creditAccountType, loanProductId, paymentTypeId, loanId, transactionId, date, amount, ...)

// 获取产品关联的 GL 科目
public GLAccount getLinkedGLAccountForLoanProduct(Long loanProductId, int accountType, Long paymentTypeId):
    → ProductToGLAccountMappingRepository.findOneByProductIdAndFinancialAccountType()
```

---

## 七、ProductToGLAccountMapping（产品→科目映射）

这是决定「谁借谁贷」的核心映射层。

```java
@Entity
@Table(name = "acc_product_to_gl_account_mapping")
public class ProductToGLAccountMapping {
    private Long id;
    private GLAccount glAccount;                    // 目标总账科目
    private PortfolioProductType productType;       // LOAN / SAVINGS / SHARES
    private Long productId;                         // 产品 ID
    private Integer financialAccountType;           // 科目类型 (如 FUND_SOURCE=1)
    private Long paymentType;                       // 支付方式 (可选，用于多资金源)
}
```

**查询方式：**
- `findOneByProductIdAndFinancialAccountTypeAndPaymentType()` — 精确匹配
- `findOneByProductIdAndFinancialAccountType()` — 无支付方式回退

---

## 八、数据流：从交易到分录的完整路径

```
  业务操作（还款/放款/应计）
       │
       ├──→ AccountingBridgeDataDTO (数据传输桥)
       │         │
       │         ├──→ AccountingProcessorHelper.populateLoanDtoFromDTO()
       │         │         将原始 DTO 转换为 LoanDTO (含 LoanTransactionDTO 列表)
       │         │
       │         ├──→ AccrualBasedAccountingProcessorForLoan.createJournalEntriesForLoan(loanDTO)
       │         │         逐个处理 LoanTransactionDTO
       │         │              │
       │         │              ├── 判断 transactionType:
       │         │              │   isDisbursement() → createJournalEntriesForDisbursements()
       │         │              │   isAccrual()       → createJournalEntriesForAccruals()
       │         │              │   isRepayment()     → createJournalEntriesForRepayments()
       │         │              │   isWriteOff()      → createJournalEntriesForWriteOffs()
       │         │              │   isRecoveryRepayment() → createJournalEntriesForRecoveryPayments()
       │         │              │   ...
       │         │              │
       │         │              └── 每个方法内部：
       │         │                  1. 确定 debit/credit 科目类型
       │         │                     (例如放款: DEBIT=FUND_SOURCE, CREDIT=LOAN_PORTFOLIO)
       │         │                  2. 通过 ProductToGLAccountMapping 查询实际 GLAccount
       │         │                  3. 调用 helper.createJournalEntriesForLoan()
       │         │                     或 helper.createCreditJournalEntryForLoan()
       │         │                     或 helper.createDebitJournalEntryForLoan()
       │         │
       │         └── 最终: JournalEntry.createNew(DEBIT/CREDIT) → JPA saveAll()
```

---

## 九、与 jv_purchase_001 基准测试相关的代码

### 9.1 相关类

| 类 | 路径 | 作用 |
|----|------|------|
| `JournalEntryDataValidator` | `fineract-accounting/.../data/JournalEntryDataValidator.java` | 运行时余额更新校验 |
| `JournalEntryData` | `fineract-accounting/.../data/JournalEntryData.java` | 分录 DTO，含 `credits`/`debits` 字段 |
| `CreditDebit` | `fineract-accounting/.../data/CreditDebit.java` | 借贷明细 DTO |
| `SingleDebitOrCreditEntryCommand` | `fineract-accounting/.../command/` | 单笔借贷命令 |
| `JournalEntryCommand` | `fineract-accounting/.../command/JournalEntryCommand.java` | 分录创建命令 |
| `JournalEntryWritePlatformService` | `fineract-provider/.../service/` | 分录写入服务接口 |
| `JournalEntryWritePlatformServiceJpaRepositoryImpl` | `fineract-provider/.../service/` | 分录写入服务实现 |

### 9.2 基准测试数据 (benchmark)

**文件：** `demo-project/benchmarks/fineract-fast/train/items.json`

```json
{
  "id": "jv_purchase_001",
  "question": "买入 A物品 花费 100.00",
  "task_type": "journal_entry",
  "context_refs": [
    "fineract-accounting/src/main/java/org/apache/fineract/accounting/journalentry/data/JournalEntryDataValidator.java"
  ],
  "expected_checks": [
    "会计凭证", "借", "贷", "库存", "银行", "100.00", "借贷校验"
  ],
  "scorer": "python_script",
  "scorer_config": {
    "script": "../score_expected_checks.py"
  }
}
```

### 9.3 分录生成逻辑要点

手工分录的关键校验（`JournalEntryCommand.validateForCreate()` / `validateBusinessRulesForJournalEntries()`）：
1. 借方总额 == 贷方总额（借贷平衡）
2. 同一 GL 科目不能在借贷双方同时出现
3. `credits[]` / `debits[]` 至少一方非空
4. 每笔分录必须有 `glAccountId` 和 `amount`

---

## 十、关键文件清单 (66 个文件)

### fineract-accounting 模块
| 文件 | 说明 |
|------|------|
| `domain/JournalEntry.java` | JPA 实体 |
| `domain/JournalEntryRepository.java` | Repository |
| `data/JournalEntryData.java` | DTO |
| `data/JournalEntryDataValidator.java` | 校验器 |
| `data/CreditDebit.java` | 借贷明细 DTO |
| `data/TransactionDetailData.java` | 交易明细 DTO |
| `data/TransactionTypeEnumData.java` | 交易类型枚举数据 |
| `data/OfficeOpeningBalancesData.java` | 期初余额数据 |
| `data/JournalEntryIdentifier.java` | 分录标识符 |
| `data/JournalEntryAssociationParametersData.java` | 关联参数 |
| `command/JournalEntryCommand.java` | 创建命令 |
| `command/SingleDebitOrCreditEntryCommand.java` | 单笔借贷命令 |
| `api/JournalEntryJsonInputParams.java` | API 参数名 |
| `serialization/JournalEntryCommandFromApiJsonDeserializer.java` | JSON 反序列化 |
| `service/JournalEntryReadPlatformService.java` | 读服务 |
| `service/JournalEntryRunningBalanceUpdateService.java` | 余额更新 |
| `exception/JournalEntriesNotFoundException.java` | 异常 |
| `exception/JournalEntryNotFoundException.java` | 异常 |
| `exception/JournalEntryInvalidException.java` | 异常 |
| `exception/JournalEntryRuntimeException.java` | 异常 |

### fineract-provider 模块
| 文件 | 说明 |
|------|------|
| `service/JournalEntryWritePlatformService.java` | 写服务接口 |
| `service/JournalEntryWritePlatformServiceJpaRepositoryImpl.java` | 写服务实现 |
| `service/AccountingProcessorHelper.java` | 辅助类（1300+ 行）|
| `service/AccrualBasedAccountingProcessorForLoan.java` | 权责制贷款处理器（2200+ 行） |
| `service/CashBasedAccountingProcessorForLoan.java` | 现金制贷款处理器 |
| `service/CashBasedAccountingProcessorForSavings.java` | 现金制储蓄处理器 |
| `service/CashBasedAccountingProcessorForClientTransactions.java` | 现金制客户交易 |
| `service/CashBasedAccountingProcessorForWorkingCapitalLoan.java` | 营运资金贷款 |
| `service/LoanCommonAccountingHelper.java` | 税金/应收 公共逻辑 |
| `service/AccountingProcessorForLoanFactory.java` | 贷款处理器工厂 |
| `service/AccountingProcessorForSavingsFactory.java` | 储蓄处理器工厂 |
| `data/LoanDTO.java` | 贷款 DTO |
| `data/LoanTransactionDTO.java` | 贷款交易 DTO |
| `data/SavingsDTO.java` | 储蓄 DTO |
| `data/SavingsTransactionDTO.java` | 储蓄交易 DTO |
| `data/SharesDTO.java` / `SharesTransactionDTO.java` | 股份 DTO |
| `data/ChargePaymentDTO.java` | 费用支付 DTO |
| `data/ChargeTaxPaymentDTO.java` | 费用税金 DTO |
| `data/ClientTransactionDTO.java` | 客户交易 DTO |

### fineract-core 模块
| 文件 | 说明 |
|------|------|
| `accounting/common/AccountingConstants.java` | 所有会计常量（585 行） |
| `accounting/journalentry/domain/JournalEntryType.java` | 凭证类型枚举 |

---

## 十一、借贷规则总结

在 Fineract 中，**借贷方向遵循标准会计准则**：

| 科目类型 | 增加 | 减少 |
|----------|------|------|
| **Asset (资产)** | 借记 (DEBIT) | 贷记 (CREDIT) |
| **Liability (负债)** | 贷记 (CREDIT) | 借记 (DEBIT) |
| **Equity (权益)** | 贷记 (CREDIT) | 借记 (DEBIT) |
| **Income (收入)** | 贷记 (CREDIT) | 借记 (DEBIT) |
| **Expense (费用)** | 借记 (DEBIT) | 贷记 (CREDIT) |

**以放款为例：**
- 借 (DEBIT): FUND_SOURCE (资产减少 = 资金流出)
- 贷 (CREDIT): LOAN_PORTFOLIO (资产增加 = 应收贷款)

**以还款为例：**
- 借 (DEBIT): FUND_SOURCE (资产增加 = 资金流入)
- 贷 (CREDIT): LOAN_PORTFOLIO (资产减少 = 贷款余额减少)
