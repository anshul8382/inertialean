# Workflow System Guide

## Overview

The monthly investment workflow system manages the complete lifecycle of client investments from fund receipt to portfolio update. This guide explains how the system works and how to maintain it for optimal performance.

## Workflow Stages

### 1. **FUNDS** (Funds Received)
- **Status**: Initial stage when client funds are received
- **Color**: Yellow/Warning
- **Actions**: Confirm fund receipt, update investment amount

### 2. **RECOS** (Recommendations Generated)
- **Status**: Investment recommendations prepared
- **Color**: Blue/Info
- **Actions**: Generate investment recommendations, enter recommendation amount
- **Special**: Requires popup for amount entry

### 3. **NOTIFY** (Client Notified)
- **Status**: Client has been notified of recommendations
- **Color**: Purple/Primary
- **Actions**: Send notifications to client

### 4. **EXEC** (Executed)
- **Status**: Trades have been executed
- **Color**: Orange/Warning
- **Actions**: Execute trades based on recommendations

### 5. **UPDATE** (Portfolio Updated)
- **Status**: Portfolio has been updated with new investments
- **Color**: Teal/Success
- **Actions**: Update client portfolio

### 6. **COMPLETED** (Completed)
- **Status**: Workflow is complete
- **Color**: Green/Success
- **Actions**: No further actions needed

## Monthly Investment Cycle

### Month Start (1st of each month)
1. **Create New Investments**: Generate monthly investments for recurring clients
2. **Initialize Workflows**: Create workflows starting at FUNDS stage
3. **Set Targets**: Establish completion targets for the month

### Throughout the Month
1. **Process Workflows**: Move investments through workflow stages
2. **Track Progress**: Monitor via daily workflow reports
3. **Handle Exceptions**: Address any issues or delays

### Month End (Last day of month)
1. **Generate Reports**: Create comprehensive monthly reports
2. **Review Performance**: Analyze completion rates and amounts
3. **Plan Next Month**: Prepare for upcoming cycle

### Cleanup (30+ days after completion)
1. **Archive Old Workflows**: Move completed workflows to archive
2. **Maintain Performance**: Keep system responsive
3. **Preserve Data**: Ensure historical data is accessible

## System Features

### 1. **Filtered View (Default)**
- **Completed workflows are hidden by default**
- **Shows only active workflows** (FUNDS through UPDATE)
- **Reduces clutter and improves performance**
- **Toggle available to show completed workflows when needed**

### 2. **Next Level Button**
- **Automatically advances workflows** through stages
- **Special handling for RECOS stage** (requires amount input)
- **Other stages advance automatically** without popup

### 3. **Daily Workflow Report**
- **Real-time dashboard** of all active workflows
- **Stage-wise grouping** with client details
- **Amount tracking** and subtotals
- **Quick action buttons** for workflow management

### 4. **Workflow Cleanup System**
- **Archive old completed workflows** (30+ days)
- **Prepare next month cycle** automatically
- **Generate monthly reports** on demand
- **Maintain system performance**

## Cleanup Process

### Why Cleanup is Necessary
1. **Performance**: Large numbers of completed workflows slow down queries
2. **Clarity**: Focus on active workflows, not historical data
3. **Maintenance**: Regular cleanup prevents system bloat
4. **Compliance**: Archive old data for audit purposes

### Cleanup Operations

#### 1. Archive Old Workflows
```bash
# Archive workflows older than 30 days
python3 workflow_cleanup.py --cleanup --days 30
```

**What it does:**
- Finds completed workflows older than specified days
- Marks them as archived
- Updates timestamps for tracking
- Preserves all data for audit purposes

#### 2. Prepare Next Month
```bash
# Prepare system for next month cycle
python3 workflow_cleanup.py --prepare-next-month
```

**What it does:**
- Identifies recurring clients
- Creates new monthly investments
- Initializes new workflows
- Sets up targets for next month

#### 3. Generate Monthly Report
```bash
# Generate report for specific month
python3 workflow_cleanup.py --monthly-report --month 8 --year 2025
```

**What it does:**
- Compiles all completed workflows for the month
- Calculates total amounts and statistics
- Provides client-wise breakdown
- Generates comprehensive report

### Web Interface
Access cleanup operations via: **Workflows → Workflow Cleanup**

## Best Practices

### 1. **Regular Maintenance**
- **Weekly**: Review daily workflow reports
- **Monthly**: Generate monthly reports before cleanup
- **Quarterly**: Review and optimize cleanup settings

### 2. **Performance Optimization**
- **Archive workflows** older than 30 days
- **Use filtered views** by default
- **Monitor system performance** regularly
- **Clean up old data** systematically

### 3. **Data Preservation**
- **Generate reports** before archiving
- **Keep backup copies** of important data
- **Document any custom processes**
- **Maintain audit trails**

### 4. **User Experience**
- **Hide completed workflows** by default
- **Provide easy toggle** to show completed
- **Use clear visual indicators** for stages
- **Offer quick actions** for common tasks

## Technical Implementation

### Database Schema
- **Workflow**: Main workflow record with current stage
- **WorkflowAction**: History of all actions taken
- **MonthlyInvestment**: Investment details and amounts
- **Client**: Client information and preferences

### Key Features
- **Automatic stage progression** with validation
- **Amount tracking** throughout the process
- **Action history** for audit trails
- **Status filtering** for performance
- **Archive system** for data management

### Security
- **CSRF protection** on all forms
- **User authentication** required
- **Action logging** for accountability
- **Data validation** at all stages

## Troubleshooting

### Common Issues

#### 1. **Workflows Not Advancing**
- Check if user has proper permissions
- Verify CSRF token is present
- Ensure all required fields are filled

#### 2. **Performance Issues**
- Archive old completed workflows
- Use filtered views by default
- Check database indexes

#### 3. **Data Inconsistencies**
- Run monthly reports to verify data
- Check workflow action history
- Validate investment amounts

### Support
For technical issues or questions about the workflow system, refer to:
- Daily workflow reports for current status
- Workflow cleanup interface for maintenance
- System logs for error details

---

**Last Updated**: August 2025
**Version**: 1.0

