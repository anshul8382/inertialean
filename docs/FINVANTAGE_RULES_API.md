# FinVantage Rules & Products API

Admin-editable rules, approved products, and insurance questionnaire.

## Rules

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/finvantage/rules` | List rules. Query: `?category=insurance` |
| POST | `/api/v1/finvantage/rules` | Create rule. Body: `{ code, name, description?, category?, priority?, productIds? }` |
| PUT | `/api/v1/finvantage/rules/:id` | Update rule |
| DELETE | `/api/v1/finvantage/rules/:id` | Delete rule |

Seeded rules: `INSURANCE_FIRST`, `EMERGENCY_BUFFER`, `RULE_3_6_12`, `INVESTMENT_HORIZON`.

## Products

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/finvantage/products` | List products. Query: `?ruleCode=INSURANCE_FIRST`, `?productType=health_insurance` |
| POST | `/api/v1/finvantage/products` | Create product. Body: `{ name, productType, provider?, minCoverage?, maxCoverage?, ruleIds? }` |
| PUT | `/api/v1/finvantage/products/:id` | Update product |

Products fulfill rules via the `finvantage_rule_product` link table.

## Insurance Questionnaire

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/finvantage/insurance-questions` | List questions (ordered). Query: `?category=health` |
| POST | `/api/v1/finvantage/insurance-questions` | Create question (admin) |
| PUT | `/api/v1/finvantage/insurance-questions/:id` | Update question |

## Insurance Assessment & Suggestions

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/finvantage/insurance-assess` | Submit answers. Body: `{ email, answers: { questionId: value } }`. Returns suggested products. |
| GET | `/api/v1/finvantage/insurance-assess?email=` | Get saved assessment and suggested products |

Suggestion logic: when `currentCoverage < ₹5L`, products linked to `INSURANCE_FIRST` rule with suitable min/max coverage are returned.
