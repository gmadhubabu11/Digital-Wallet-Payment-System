ADMIN, USER, MERCHANT = "Admin", "User", "Merchant"

KYC_MIN, KYC_FULL = "Minimum", "Full"
WALLET_ACTIVE, WALLET_FROZEN = "Active", "Frozen"

# account types
ACC_USER, ACC_MERCHANT = "User Wallet", "Merchant Wallet"
ACC_BANK, ACC_CASHBACK, ACC_FEES = "Bank Settlement", "Cashback Pool", "Fees"
ACC_CLEARING, ACC_HOLD = "Withdrawal Clearing", "Hold"
SYSTEM_ACCOUNT_TYPES = [ACC_BANK, ACC_CASHBACK, ACC_FEES, ACC_CLEARING, ACC_HOLD]

# transaction types
T_ADD, T_P2P, T_MPAY, T_REFUND = "Add Money", "P2P Transfer", "Merchant Payment", "Merchant Refund"
T_CASHBACK, T_WITHDRAW, T_WREV, T_REV = "Cashback", "Withdrawal", "Withdrawal Reversal", "Reversal"
TXN_TYPES = [T_ADD, T_P2P, T_MPAY, T_REFUND, T_CASHBACK, T_WITHDRAW, T_WREV, T_REV]
SENDING_TYPES = (T_P2P, T_MPAY, T_WITHDRAW)

# transaction statuses
S_PENDING, S_SUCCESS, S_FAILED, S_HOLD, S_REVERSED = "Pending", "Success", "Failed", "On Hold", "Reversed"
COUNTED_STATUSES = (S_SUCCESS, S_PENDING, S_HOLD)

DEBIT, CREDIT = "Debit", "Credit"

OTP_TRANSFER, OTP_BANK, OTP_PIN = "High Value Transfer", "Add Bank Account", "PIN Reset"

OTP_THRESHOLD = 5000
FLAG_AMOUNT = 20000
