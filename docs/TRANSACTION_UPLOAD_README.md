# Transaction Upload Utility

## Overview

The Transaction Upload Utility allows users to bulk upload transaction data from Excel (.xlsx, .xls) or CSV files. This feature is integrated into the "Add Transactions" page and provides a convenient way to import multiple transactions at once.

## Features

- **File Upload**: Support for Excel (.xlsx, .xls) and CSV files
- **Template Download**: Download a sample Excel template with the correct format
- **Data Validation**: Comprehensive validation of uploaded data
- **Error Handling**: Detailed error messages for invalid data
- **Automatic Processing**: Automatic calculation of holdings and cashflows
- **Flexible Date Formats**: Support for multiple date formats
- **Currency Handling**: Automatic handling of currency symbols and formatting

## File Format Requirements

### Required Columns

The uploaded file must contain the following columns (exact names required):

1. **Date**: Transaction date
2. **Type**: Transaction type (BUY, SELL, SPLIT, or BONUS)
3. **Stock**: Stock symbol
4. **Transacted Units**: Number of units traded
5. **Transacted Price (per unit)**: Price per unit

### Supported Date Formats

- `7-Mar-2025` (DD-MMM-YYYY)
- `2025-03-07` (YYYY-MM-DD)
- `07/03/2025` (DD/MM/YYYY)
- `07-03-2025` (DD-MM-YYYY)
- `03/07/2025` (MM/DD/YYYY)

### Supported Stock Symbol Formats

- `ICICIBANK` (plain symbol)
- `NSE:ICICIBANK` (with exchange prefix)
- `BSE:INFY` (with exchange prefix)
- `NSE: TCS` (with space after prefix)

### Supported Price Formats

- `₹1,214.55` (with currency symbol and commas)
- `1,214.55` (with commas)
- `1214.55` (plain number)
- `1500` (integer)
- `0` or `₹0.00` (for SPLIT and BONUS transactions)

## Sample Data

Here's an example of the expected file format:

| Date | Type | Stock | Transacted Units | Transacted Price (per unit) |
|------|------|-------|------------------|------------------------------|
| 7-Mar-2025 | Buy | NSE:ICICIBANK | 25.0 | ₹1,214.55 |
| 8-Mar-2025 | Sell | NSE:INFY | 10.0 | ₹1,500.00 |
| 9-Mar-2025 | Split | NSE:TCS | 2.0 | ₹0.00 |
| 10-Mar-2025 | Bonus | NSE:RELIANCE | 1.0 | ₹0.00 |
| 11-Mar-2025 | Buy | NSE:HDFC | 15.0 | ₹3,200.75 |

## Transaction Types

### Regular Transactions
- **BUY**: Purchase of securities (outflow of cash)
- **SELL**: Sale of securities (inflow of cash)

### Corporate Actions
- **SPLIT**: Stock split transaction
  - **Transacted Units**: Split ratio (e.g., 2 for 2:1 split, 5 for 5:1 split)
  - **Transacted Price**: Always 0 (no money changes hands)
  - **Effect**: Existing quantity is multiplied by split ratio, average price is divided by split ratio
  
- **BONUS**: Bonus share issue
  - **Transacted Units**: Bonus ratio (e.g., 1 for 1:1 bonus, 2 for 2:1 bonus)
  - **Transacted Price**: Always 0 (no money changes hands)
  - **Effect**: Bonus quantity (current holding × bonus ratio) is added to existing holding, average price remains unchanged

### Examples
- **2:1 Split**: Transacted Units = 2, Transacted Price = 0
  - If you had 100 shares at ₹100 each, after split you'll have 200 shares at ₹50 each
- **1:1 Bonus**: Transacted Units = 1, Transacted Price = 0
  - If you had 100 shares at ₹100 each, after bonus you'll have 200 shares at ₹100 each

## How to Use

### 1. Access the Upload Feature

1. Navigate to **Transactions** → **Add Transaction**
2. Scroll down to the "Upload Transaction File" section

### 2. Download Template (Optional)

1. Click the "Download Template" button
2. Open the downloaded Excel file
3. Replace the sample data with your actual transaction data
4. Save the file

### 3. Upload Your File

1. Select the client from the dropdown
2. Click "Choose File" and select your Excel or CSV file
3. Click "Upload Transactions"
4. Wait for processing to complete

### 4. Review Results

- Success message will show the number of transactions uploaded
- Error messages will detail any issues with the file

## Error Handling

### Common Errors and Solutions

1. **Missing Required Columns**
   - Ensure all 5 required columns are present
   - Check column names match exactly (case-sensitive)

2. **Invalid Date Format**
   - Use one of the supported date formats
   - Ensure dates are valid (not future dates unless intended)

3. **Invalid Transaction Type**
   - Use only "BUY", "SELL", "SPLIT", or "BONUS" (case-insensitive)

4. **Stock Symbol Not Found**
   - Ensure the stock symbol exists in the system
   - Check for typos in the symbol
   - Remove exchange prefixes if the symbol doesn't match

5. **Invalid Quantity or Price**
   - Ensure both values are positive numbers
   - Remove currency symbols and commas from price
   - For SPLIT and BONUS transactions, price should be 0

## Technical Implementation

### Backend Processing

1. **File Validation**: Checks file type and required columns
2. **Data Parsing**: Converts strings to appropriate data types
3. **Security Lookup**: Matches stock symbols to existing securities
4. **Transaction Creation**: Creates Transaction records in the database
5. **Holdings Update**: Recalculates holdings for the client
6. **Cashflow Update**: Updates cashflow records

### Database Updates

The upload process automatically:

- Creates new Transaction records
- Updates or creates Holding records
- Updates Cashflow records
- Maintains data consistency across all related tables

### Error Recovery

- If any row has errors, the entire upload is rolled back
- Detailed error messages help identify and fix issues
- No partial uploads are allowed to maintain data integrity

## Security Considerations

- File uploads are validated for type and content
- File names are sanitized using `secure_filename`
- Only authenticated users can access the upload feature
- File processing is done server-side with proper error handling

## Performance Considerations

- Large files are processed row by row to manage memory usage
- Database transactions are used to ensure data consistency
- Error handling prevents partial uploads that could corrupt data

## Troubleshooting

### File Won't Upload

1. Check file format (must be .xlsx, .xls, or .csv)
2. Ensure file is not corrupted
3. Check file size (should be reasonable for transaction data)

### Processing Errors

1. Review error messages carefully
2. Check data format against requirements
3. Verify stock symbols exist in the system
4. Ensure all required fields are filled

### Data Not Appearing

1. Check if upload was successful (look for success message)
2. Verify client selection was correct
3. Check transaction list for the selected client
4. Review holdings and cashflows for updates

## Support

For issues with the transaction upload feature:

1. Check this documentation first
2. Review error messages in the application
3. Verify your file format matches the requirements
4. Contact system administrator if issues persist

## Future Enhancements

Potential improvements for future versions:

- Support for additional file formats
- Batch processing for very large files
- Preview functionality before upload
- Template customization options
- Integration with external data sources
