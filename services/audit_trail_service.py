"""
File-Based Audit Trail Service for Enhanced Review Generation
Creates downloadable audit files with complete step-by-step analysis
"""
import os
import json
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)

class AuditTrailService:
    """
    File-based audit trail service that creates downloadable audit files
    for enhanced review generation with complete step-by-step analysis
    """
    
    def __init__(self, audit_dir: str = "audit_trails"):
        self.audit_dir = audit_dir
        self.session_id = None
        self.session_data = {
            'session_id': None,
            'client_id': None,
            'start_date': None,
            'end_date': None,
            'start_time': None,
            'steps': [],
            'errors': [],
            'summary': {}
        }
        
        # Ensure audit directory exists
        os.makedirs(audit_dir, exist_ok=True)
    
    def start_audit_session(self, client_id: int, start_date: str = None, end_date: str = None) -> str:
        """
        Start a new audit session and return session ID
        
        Args:
            client_id: Client ID for the review
            start_date: Start date for analysis (optional)
            end_date: End date for analysis (optional)
            
        Returns:
            session_id: Unique session identifier
        """
        self.session_id = str(uuid.uuid4())
        self.session_data.update({
            'session_id': self.session_id,
            'client_id': client_id,
            'start_date': start_date,
            'end_date': end_date,
            'start_time': datetime.now().isoformat(),
            'steps': [],
            'errors': [],
            'summary': {
                'total_steps': 0,
                'completed_steps': 0,
                'failed_steps': 0,
                'total_duration_ms': 0
            }
        })
        
        logger.info(f"Started audit session {self.session_id} for client {client_id}")
        return self.session_id
    
    def log_step(self, step_name: str, step_data: Dict[str, Any], 
                 success: bool = True, duration_ms: int = 0, 
                 error_message: str = None) -> None:
        """
        Log a step in the audit trail
        
        Args:
            step_name: Name of the step (e.g., 'data_retrieval_cashflows')
            step_data: Data associated with this step
            success: Whether the step succeeded
            duration_ms: Duration of the step in milliseconds
            error_message: Error message if step failed
        """
        if not self.session_id:
            logger.warning("No active audit session. Call start_audit_session first.")
            return
        
        step = {
            'step_name': step_name,
            'timestamp': datetime.now().isoformat(),
            'success': success,
            'duration_ms': duration_ms,
            'data_summary': self._create_data_summary(step_data),
            'raw_data': step_data,
            'error_message': error_message
        }
        
        self.session_data['steps'].append(step)
        self.session_data['summary']['total_steps'] += 1
        
        if success:
            self.session_data['summary']['completed_steps'] += 1
        else:
            self.session_data['summary']['failed_steps'] += 1
            if error_message:
                self.session_data['errors'].append({
                    'step_name': step_name,
                    'timestamp': datetime.now().isoformat(),
                    'error_message': error_message,
                    'step_data': step_data
                })
        
        logger.info(f"Logged step '{step_name}' - Success: {success}, Duration: {duration_ms}ms")
    
    def log_data_retrieval(self, data_type: str, record_count: int, 
                          query_details: str = None, raw_data: Any = None) -> None:
        """
        Log data retrieval step
        
        Args:
            data_type: Type of data retrieved (e.g., 'cashflows', 'holdings', 'transactions')
            record_count: Number of records retrieved
            query_details: Details about the query used
            raw_data: Raw data retrieved (optional, for debugging)
        """
        step_data = {
            'data_type': data_type,
            'record_count': record_count,
            'query_details': query_details,
            'sample_records': self._get_sample_records(raw_data) if raw_data else None
        }
        
        self.log_step(f"data_retrieval_{data_type}", step_data, success=True)
    
    def log_calculation(self, calculation_type: str, inputs: Dict[str, Any], 
                       outputs: Dict[str, Any], formula: str = None) -> None:
        """
        Log a calculation step
        
        Args:
            calculation_type: Type of calculation (e.g., 'xirr', 'returns', 'risk_metrics')
            inputs: Input data for calculation
            outputs: Output results
            formula: Formula or method used (optional)
        """
        step_data = {
            'calculation_type': calculation_type,
            'inputs': inputs,
            'outputs': outputs,
            'formula': formula
        }
        
        self.log_step(f"calculation_{calculation_type}", step_data, success=True)
    
    def log_api_call(self, api_endpoint: str, request_params: Dict[str, Any], 
                    response_data: Any, success: bool = True, 
                    duration_ms: int = 0) -> None:
        """
        Log an API call
        
        Args:
            api_endpoint: API endpoint called
            request_params: Parameters sent to API
            response_data: Response data received
            success: Whether API call succeeded
            duration_ms: Duration of API call
        """
        step_data = {
            'api_endpoint': api_endpoint,
            'request_params': request_params,
            'response_summary': self._create_data_summary(response_data),
            'raw_response': response_data
        }
        
        self.log_step(f"api_call_{api_endpoint.replace('/', '_')}", step_data, 
                     success=success, duration_ms=duration_ms)
    
    def complete_audit_session(self, final_result: Dict[str, Any] = None, 
                              total_duration_ms: int = 0) -> str:
        """
        Complete the audit session and save to file
        
        Args:
            final_result: Final result of the process
            total_duration_ms: Total duration of the entire process
            
        Returns:
            file_path: Path to the saved audit file
        """
        if not self.session_id:
            logger.warning("No active audit session to complete")
            return None
        
        # Update session summary
        self.session_data['summary'].update({
            'total_duration_ms': total_duration_ms,
            'end_time': datetime.now().isoformat(),
            'final_result_summary': self._create_data_summary(final_result) if final_result else None
        })
        
        # Save to file
        file_path = self._save_audit_file()
        
        logger.info(f"Completed audit session {self.session_id}. File saved: {file_path}")
        return file_path
    
    def _create_data_summary(self, data: Any) -> Dict[str, Any]:
        """
        Create a summary of data for logging
        """
        if data is None:
            return {'type': 'null', 'size': 0}
        
        if isinstance(data, (list, tuple)):
            return {
                'type': 'list',
                'length': len(data),
                'sample_items': data[:3] if len(data) > 3 else data
            }
        
        if isinstance(data, dict):
            return {
                'type': 'dict',
                'keys': list(data.keys()),
                'key_count': len(data)
            }
        
        if isinstance(data, (int, float)):
            return {
                'type': 'numeric',
                'value': data
            }
        
        return {
            'type': str(type(data).__name__),
            'value': str(data)[:100] + '...' if len(str(data)) > 100 else str(data)
        }
    
    def _get_sample_records(self, data: Any, max_samples: int = 3) -> List[Any]:
        """
        Get sample records from data for logging
        """
        if isinstance(data, (list, tuple)) and len(data) > 0:
            return data[:max_samples]
        return []
    
    def _save_audit_file(self) -> str:
        """
        Save audit session data to file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"audit_{self.session_id}_{timestamp}.json"
        file_path = os.path.join(self.audit_dir, filename)
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.session_data, f, indent=2, ensure_ascii=False, default=str)
            
            return file_path
        except Exception as e:
            logger.error(f"Error saving audit file: {str(e)}")
            return None
    
    def get_audit_file_path(self, session_id: str) -> Optional[str]:
        """
        Get the file path for an audit session
        """
        if not os.path.exists(self.audit_dir):
            return None
        
        for filename in os.listdir(self.audit_dir):
            if session_id in filename:
                return os.path.join(self.audit_dir, filename)
        
        return None
    
    def create_calculation_file(self, session_id: str, calculation_type: str, 
                               data: Dict[str, Any]) -> str:
        """
        Create a downloadable calculation file for audit purposes
        
        Args:
            session_id: Audit session ID
            calculation_type: Type of calculation (e.g., 'xirr', 'returns', 'risk')
            data: Calculation data to include
            
        Returns:
            file_path: Path to the calculation file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"calculations_{calculation_type}_{session_id}_{timestamp}.json"
        file_path = os.path.join(self.audit_dir, filename)
        
        calculation_data = {
            'session_id': session_id,
            'calculation_type': calculation_type,
            'timestamp': datetime.now().isoformat(),
            'data': data
        }
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(calculation_data, f, indent=2, ensure_ascii=False, default=str)
            
            logger.info(f"Created calculation file: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Error creating calculation file: {str(e)}")
            return None
    
    def create_summary_report(self, session_id: str) -> str:
        """
        Create a human-readable summary report
        
        Args:
            session_id: Audit session ID
            
        Returns:
            file_path: Path to the summary report
        """
        audit_file = self.get_audit_file_path(session_id)
        if not audit_file:
            return None
        
        try:
            with open(audit_file, 'r', encoding='utf-8') as f:
                audit_data = json.load(f)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"summary_report_{session_id}_{timestamp}.txt"
            file_path = os.path.join(self.audit_dir, filename)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("ENHANCED REVIEW GENERATION - AUDIT SUMMARY\n")
                f.write("=" * 50 + "\n\n")
                
                f.write(f"Session ID: {audit_data['session_id']}\n")
                f.write(f"Client ID: {audit_data['client_id']}\n")
                f.write(f"Start Date: {audit_data['start_date']}\n")
                f.write(f"End Date: {audit_data['end_date']}\n")
                f.write(f"Start Time: {audit_data['start_time']}\n")
                f.write(f"End Time: {audit_data['summary']['end_time']}\n")
                f.write(f"Total Duration: {audit_data['summary']['total_duration_ms']} ms\n\n")
                
                f.write("STEP SUMMARY:\n")
                f.write("-" * 20 + "\n")
                f.write(f"Total Steps: {audit_data['summary']['total_steps']}\n")
                f.write(f"Completed: {audit_data['summary']['completed_steps']}\n")
                f.write(f"Failed: {audit_data['summary']['failed_steps']}\n\n")
                
                f.write("DETAILED STEPS:\n")
                f.write("-" * 20 + "\n")
                for i, step in enumerate(audit_data['steps'], 1):
                    f.write(f"{i}. {step['step_name']}\n")
                    f.write(f"   Status: {'SUCCESS' if step['success'] else 'FAILED'}\n")
                    f.write(f"   Duration: {step['duration_ms']} ms\n")
                    if step['error_message']:
                        f.write(f"   Error: {step['error_message']}\n")
                    f.write("\n")
                
                if audit_data['errors']:
                    f.write("ERRORS:\n")
                    f.write("-" * 10 + "\n")
                    for error in audit_data['errors']:
                        f.write(f"Step: {error['step_name']}\n")
                        f.write(f"Error: {error['error_message']}\n")
                        f.write(f"Time: {error['timestamp']}\n\n")
            
            logger.info(f"Created summary report: {file_path}")
            return file_path
            
        except Exception as e:
            logger.error(f"Error creating summary report: {str(e)}")
            return None