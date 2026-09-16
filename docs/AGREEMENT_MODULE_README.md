# Agreement Module for Leads Workflow

This module provides a comprehensive solution for managing agreement templates and generating personalized PDF agreements for leads in your investment management system.

## Features

### 1. Template Management
- **Upload Templates**: Support for .doc, .docx, .pdf, .html, and .txt files
- **Variable Detection**: Automatically detects variables marked as `<<variable_name>>` in templates
- **Template Organization**: Categorize and manage multiple agreement templates
- **Template Preview**: View detected variables before saving

### 2. Agreement Generation
- **Dynamic Forms**: Create forms based on template variables
- **Variable Storage**: Store filled variables in a dedicated database table
- **PDF Generation**: Generate personalized PDF agreements
- **Status Tracking**: Track agreement status (draft, generated, sent, signed, completed)

### 3. Lead Integration
- **Lead-Specific Agreements**: Create agreements tied to specific leads
- **Client Association**: Link agreements to converted clients
- **Workflow Integration**: Seamlessly integrated with existing lead workflow

## Database Schema

### Tables Created

1. **agreement_template**
   - Stores template files and metadata
   - Tracks detected variables
   - Manages template status (active/inactive)

2. **agreement**
   - Links agreements to leads and templates
   - Stores generated PDF paths
   - Tracks agreement status and timestamps

3. **agreement_variables**
   - Stores individual variable values for each agreement
   - Links to leads and clients
   - Supports different variable types

## Installation

### 1. Database Setup
Run the SQL commands in `create_agreement_tables.sql` in your phpMyAdmin:

```sql
-- Create agreement_template table
CREATE TABLE `agreement_template` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(200) NOT NULL,
  `description` text,
  `template_file_path` varchar(500) NOT NULL,
  `template_type` varchar(50) NOT NULL,
  `variables` text,
  `is_active` tinyint(1) DEFAULT 1,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `created_by` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_agreement_template_created_by` (`created_by`),
  CONSTRAINT `fk_agreement_template_created_by` FOREIGN KEY (`created_by`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Create agreement table
CREATE TABLE `agreement` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `lead_id` int(11) NOT NULL,
  `template_id` int(11) NOT NULL,
  `agreement_data` text,
  `generated_pdf_path` varchar(500),
  `status` varchar(50) DEFAULT 'draft',
  `sent_date` datetime,
  `signed_date` datetime,
  `notes` text,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `created_by` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_agreement_lead_id` (`lead_id`),
  KEY `fk_agreement_template_id` (`template_id`),
  KEY `fk_agreement_created_by` (`created_by`),
  CONSTRAINT `fk_agreement_lead_id` FOREIGN KEY (`lead_id`) REFERENCES `lead` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_agreement_template_id` FOREIGN KEY (`template_id`) REFERENCES `agreement_template` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_agreement_created_by` FOREIGN KEY (`created_by`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Create agreement_variables table
CREATE TABLE `agreement_variables` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `agreement_id` int(11) NOT NULL,
  `lead_id` int(11) NOT NULL,
  `client_id` int(11) NULL,
  `variable_name` varchar(100) NOT NULL,
  `variable_value` text,
  `variable_type` varchar(50) DEFAULT 'text',
  `is_required` tinyint(1) DEFAULT 1,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `fk_agreement_variables_agreement_id` (`agreement_id`),
  KEY `fk_agreement_variables_lead_id` (`lead_id`),
  KEY `fk_agreement_variables_client_id` (`client_id`),
  KEY `idx_agreement_variable` (`agreement_id`, `variable_name`),
  CONSTRAINT `fk_agreement_variables_agreement_id` FOREIGN KEY (`agreement_id`) REFERENCES `agreement` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_agreement_variables_lead_id` FOREIGN KEY (`lead_id`) REFERENCES `lead` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_agreement_variables_client_id` FOREIGN KEY (`client_id`) REFERENCES `client` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 2. Install Dependencies
Install the required Python packages:

```bash
pip install reportlab python-docx PyPDF2 weasyprint
```

Or update your requirements.txt and run:

```bash
pip install -r requirements.txt
```

## Usage Guide

### 1. Creating Templates

1. **Access Template Management**:
   - Navigate to `/agreements/templates`
   - Click "Upload New Template"

2. **Upload Template File**:
   - Choose a file (.doc, .docx, .pdf, .html, .txt)
   - Add template name and description
   - Select template type

3. **Variable Detection**:
   - The system automatically detects variables marked as `<<variable_name>>`
   - Preview detected variables before saving
   - Variables are stored in JSON format

### 2. Creating Agreements for Leads

1. **Access Lead Agreements**:
   - Go to any lead detail page
   - Click the "Agreements" button
   - Or navigate to `/agreements/leads/{lead_id}/agreements`

2. **Select Template**:
   - Choose from available templates
   - View template details and variables
   - Create new agreement

3. **Fill Variables**:
   - Dynamic form based on template variables
   - Real-time preview of filled content
   - Save variable values

4. **Generate PDF**:
   - System generates personalized PDF
   - Download generated agreement
   - Track agreement status

### 3. Managing Agreements

1. **View Agreement Details**:
   - See all filled variables
   - Download generated PDF
   - Update agreement status

2. **Status Management**:
   - Draft: Initial state
   - Generated: PDF created
   - Sent: Agreement sent to client
   - Signed: Client signed
   - Completed: Process finished

## Template Variable Format

### Supported Variable Syntax
- Use `<<variable_name>>` format in your documents
- Variables are case-sensitive
- Spaces and special characters are supported
- Examples:
  - `<<client_name>>`
  - `<<investment_amount>>`
  - `<<agreement_date>>`
  - `<<advisor_name>>`

### Template Examples

**Investment Agreement Template:**
```
INVESTMENT AGREEMENT

This agreement is made between <<client_name>> and <<company_name>> on <<agreement_date>>.

Investment Amount: <<investment_amount>>
Investment Period: <<investment_period>>
Risk Profile: <<risk_profile>>

Signed by: <<client_signature>>
Date: <<signature_date>>
```

## File Structure

```
├── routes/
│   └── agreements.py              # Main agreement routes
├── templates/
│   └── agreements/
│       ├── templates.html         # Template listing
│       ├── create_template.html   # Template upload
│       ├── upload_template.html   # Template upload (alternative)
│       ├── lead_agreements.html   # Lead agreements listing
│       ├── create_agreement.html  # Create new agreement
│       ├── fill_variables.html    # Fill agreement variables
│       └── view_agreement.html    # View agreement details
├── models.py                      # Database models (updated)
├── routes/forms.py                # Forms (updated)
├── main.py                        # App configuration (updated)
└── create_agreement_tables.sql    # Database setup
```

## API Endpoints

### Template Management
- `GET /agreements/templates` - List all templates
- `GET /agreements/templates/new` - Create new template form
- `POST /agreements/templates/new` - Save new template
- `GET /agreements/templates/{id}` - View template details
- `GET /agreements/templates/{id}/edit` - Edit template form
- `POST /agreements/templates/{id}/edit` - Update template
- `POST /agreements/templates/{id}/delete` - Delete template
- `POST /agreements/templates/analyze` - Analyze template file

### Agreement Management
- `GET /agreements/leads/{lead_id}/agreements` - List lead agreements
- `GET /agreements/leads/{lead_id}/agreements/new` - Create agreement form
- `POST /agreements/leads/{lead_id}/agreements/new` - Save new agreement
- `GET /agreements/{id}` - View agreement details
- `GET /agreements/{id}/variables` - Fill variables form
- `POST /agreements/{id}/variables` - Save variables and generate PDF
- `GET /agreements/{id}/download` - Download generated PDF
- `POST /agreements/{id}/status` - Update agreement status

## PDF Generation

The system supports multiple PDF generation methods:

1. **WeasyPrint** (Primary): HTML to PDF conversion
2. **ReportLab** (Fallback): Direct PDF generation
3. **Template Processing**: Variable replacement in content

### PDF Generation Process
1. Read template file content
2. Replace variables with user-provided values
3. Generate HTML with styling
4. Convert to PDF using WeasyPrint or ReportLab
5. Save to file system
6. Update database with file path

## Security Features

- **File Upload Validation**: Restricted file types and sizes
- **CSRF Protection**: All forms protected against CSRF attacks
- **Authentication Required**: All routes require user login
- **File Path Security**: Secure file naming and storage
- **Input Validation**: Form validation and sanitization

## Troubleshooting

### Common Issues

1. **Template Variables Not Detected**:
   - Ensure variables use `<<variable_name>>` format
   - Check file encoding (UTF-8 recommended)
   - Verify file format is supported

2. **PDF Generation Fails**:
   - Install required dependencies (reportlab, weasyprint)
   - Check file permissions for upload directory
   - Verify template file is readable

3. **File Upload Issues**:
   - Check file size limits
   - Verify file type is supported
   - Ensure upload directory exists and is writable

### Debug Mode
Enable debug logging to troubleshoot issues:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Future Enhancements

1. **Email Integration**: Send agreements via email
2. **Digital Signatures**: E-signature integration
3. **Template Categories**: Organize templates by type
4. **Bulk Operations**: Generate multiple agreements
5. **Version Control**: Template versioning
6. **Advanced Variables**: Date pickers, dropdowns, etc.
7. **Template Preview**: Live preview with sample data

## Support

For issues or questions:
1. Check the troubleshooting section
2. Review application logs
3. Verify database setup
4. Test with simple templates first

## License

This module is part of the Inertia Investment Management System.
