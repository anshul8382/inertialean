#!/usr/bin/env python3
"""
ML Routes - Flask routes for ML recommendation system
"""
from flask import Blueprint, request, jsonify, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from models import Client, Review, User
from ml_review_service import MLReviewService
from ml_feedback_service import MLFeedbackService
from extensions import db
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

ml_bp = Blueprint('ml', __name__)

@ml_bp.route('/ml/recommendations/<int:review_id>')
@login_required
def get_ml_recommendations(review_id):
    """Get ML recommendations for a specific review"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access permissions
        if not current_user.has_role('admin') and review.user_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        # Get ML recommendations
        ml_service = MLReviewService()
        
        # Check if ML recommendations already exist
        if review.review_data and 'ml_recommendations' in review.review_data:
            ml_data = review.review_data['ml_recommendations']
        else:
            # Generate new ML recommendations
            success = ml_service.enhance_review_with_ml_recommendations(review_id)
            if success:
                review = Review.query.get(review_id)  # Refresh data
                ml_data = review.review_data.get('ml_recommendations', {})
            else:
                flash('Error generating ML recommendations', 'error')
                return redirect(url_for('review.review_detail', review_id=review_id))
        
        return render_template('ml/recommendations.html', 
                             review=review, 
                             ml_data=ml_data)
        
    except Exception as e:
        logger.error(f"Error getting ML recommendations: {str(e)}")
        flash('Error loading ML recommendations', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@ml_bp.route('/ml/train-models', methods=['POST'])
@login_required
def train_ml_models():
    """Train ML models with historical data"""
    try:
        if not current_user.has_role('admin'):
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        ml_service = MLReviewService()
        success = ml_service.train_models_with_historical_data()
        
        if success:
            flash('ML models trained successfully!', 'success')
        else:
            flash('Error training ML models. Check logs for details.', 'error')
        
        return redirect(url_for('ml.ml_dashboard'))
        
    except Exception as e:
        logger.error(f"Error training ML models: {str(e)}")
        flash('Error training ML models', 'error')
        return redirect(url_for('ml.ml_dashboard'))

@ml_bp.route('/ml/dashboard')
@login_required
def ml_dashboard():
    """ML dashboard for administrators"""
    try:
        if not current_user.has_role('admin'):
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        # Get ML statistics
        ml_service = MLReviewService()
        
        # Count reviews with ML recommendations
        reviews_with_ml = Review.query.filter(
            Review.review_data.isnot(None)
        ).all()
        
        ml_enhanced_count = 0
        total_reviews = len(reviews_with_ml)
        
        for review in reviews_with_ml:
            if review.review_data and 'ml_recommendations' in review.review_data:
                ml_enhanced_count += 1
        
        # Get recent ML-enhanced reviews
        recent_ml_reviews = Review.query.filter(
            Review.review_data.isnot(None),
            Review.status == 'completed'
        ).order_by(Review.completed_at.desc()).limit(10).all()
        
        ml_reviews = []
        for review in recent_ml_reviews:
            if review.review_data and 'ml_recommendations' in review.review_data:
                ml_data = review.review_data['ml_recommendations']
                ml_reviews.append({
                    'review': review,
                    'recommendation_count': len(ml_data.get('recommendations', [])),
                    'high_priority_count': len([r for r in ml_data.get('recommendations', []) 
                                              if r.get('priority') == 'high'])
                })
        
        # Check if models are trained
        models_trained = ml_service.load_trained_models()
        
        stats = {
            'total_reviews': total_reviews,
            'ml_enhanced_count': ml_enhanced_count,
            'ml_coverage_percent': (ml_enhanced_count / total_reviews * 100) if total_reviews > 0 else 0,
            'models_trained': models_trained,
            'recent_ml_reviews': ml_reviews[:5]
        }
        
        return render_template('ml/dashboard.html', stats=stats)
        
    except Exception as e:
        logger.error(f"Error loading ML dashboard: {str(e)}")
        flash('Error loading ML dashboard', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@ml_bp.route('/api/ml/recommendations/<int:review_id>')
@login_required
def api_ml_recommendations(review_id):
    """API endpoint for ML recommendations"""
    try:
        review = Review.query.get_or_404(review_id)
        
        # Check access permissions
        if not current_user.has_role('admin') and review.user_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get or generate ML recommendations
        ml_service = MLReviewService()
        
        if review.review_data and 'ml_recommendations' in review.review_data:
            ml_data = review.review_data['ml_recommendations']
        else:
            success = ml_service.enhance_review_with_ml_recommendations(review_id)
            if success:
                review = Review.query.get(review_id)
                ml_data = review.review_data.get('ml_recommendations', {})
            else:
                return jsonify({'error': 'Failed to generate ML recommendations'}), 500
        
        return jsonify({
            'success': True,
            'review_id': review_id,
            'ml_recommendations': ml_data
        })
        
    except Exception as e:
        logger.error(f"Error in ML recommendations API: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@ml_bp.route('/api/ml/enhance-all-reviews', methods=['POST'])
@login_required
def api_enhance_all_reviews():
    """API endpoint to enhance all completed reviews with ML recommendations"""
    try:
        if not current_user.has_role('admin'):
            return jsonify({'error': 'Access denied'}), 403
        
        # Get all completed reviews without ML recommendations
        reviews = Review.query.filter(
            Review.status == 'completed',
            Review.review_data.isnot(None)
        ).all()
        
        ml_service = MLReviewService()
        enhanced_count = 0
        failed_count = 0
        
        for review in reviews:
            if not review.review_data or 'ml_recommendations' not in review.review_data:
                success = ml_service.enhance_review_with_ml_recommendations(review.id)
                if success:
                    enhanced_count += 1
                else:
                    failed_count += 1
        
        return jsonify({
            'success': True,
            'enhanced_count': enhanced_count,
            'failed_count': failed_count,
            'total_processed': enhanced_count + failed_count
        })
        
    except Exception as e:
        logger.error(f"Error enhancing all reviews: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@ml_bp.route('/ml/portfolio-analysis/<int:client_id>')
@login_required
def portfolio_ml_analysis(client_id):
    """ML analysis for a specific client portfolio"""
    try:
        client = Client.query.get_or_404(client_id)
        
        # Check access permissions
        if not current_user.has_role('admin') and client.advisor_id != current_user.id:
            flash('Access denied', 'error')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        # Get client's latest review
        latest_review = Review.query.filter_by(
            client_id=client_id,
            status='completed'
        ).order_by(Review.completed_at.desc()).first()
        
        if not latest_review:
            flash('No completed reviews found for this client', 'warning')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        # Get ML recommendations
        ml_service = MLReviewService()
        
        if latest_review.review_data and 'ml_recommendations' in latest_review.review_data:
            ml_data = latest_review.review_data['ml_recommendations']
        else:
            # Generate ML recommendations
            success = ml_service.enhance_review_with_ml_recommendations(latest_review.id)
            if success:
                latest_review = Review.query.get(latest_review.id)
                ml_data = latest_review.review_data.get('ml_recommendations', {})
            else:
                flash('Error generating ML analysis', 'error')
                return redirect(url_for('review.review_detail', review_id=latest_review.id))
        
        return render_template('ml/portfolio_analysis.html',
                             client=client,
                             review=latest_review,
                             ml_data=ml_data)
        
    except Exception as e:
        logger.error(f"Error in portfolio ML analysis: {str(e)}")
        flash('Error loading portfolio analysis', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@ml_bp.route('/api/ml/feedback', methods=['POST'])
@login_required
def submit_recommendation_feedback():
    """Submit feedback on a specific recommendation"""
    try:
        data = request.get_json()
        
        review_id = data.get('review_id')
        recommendation_id = data.get('recommendation_id')
        rating = data.get('rating')  # 1-5 scale
        implemented = data.get('implemented')  # boolean
        ignored = data.get('ignored')  # boolean
        feedback_text = data.get('feedback_text')
        
        if not review_id or not recommendation_id:
            return jsonify({'error': 'Missing required fields'}), 400
        
        feedback_service = MLFeedbackService()
        success = feedback_service.record_recommendation_feedback(
            review_id=review_id,
            recommendation_id=recommendation_id,
            user_id=current_user.id,
            rating=rating,
            implemented=implemented,
            ignored=ignored,
            feedback_text=feedback_text
        )
        
        if success:
            return jsonify({'success': True, 'message': 'Feedback recorded successfully'})
        else:
            return jsonify({'error': 'Failed to record feedback'}), 500
            
    except Exception as e:
        logger.error(f"Error submitting feedback: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@ml_bp.route('/api/ml/outcome-feedback', methods=['POST'])
@login_required
def submit_outcome_feedback():
    """Submit outcome feedback after implementing a recommendation"""
    try:
        data = request.get_json()
        
        review_id = data.get('review_id')
        recommendation_id = data.get('recommendation_id')
        outcome_rating = data.get('outcome_rating')  # 1-5 scale
        outcome_notes = data.get('outcome_notes')
        
        if not review_id or not recommendation_id or not outcome_rating:
            return jsonify({'error': 'Missing required fields'}), 400
        
        feedback_service = MLFeedbackService()
        success = feedback_service.record_outcome_feedback(
            review_id=review_id,
            recommendation_id=recommendation_id,
            user_id=current_user.id,
            outcome_rating=outcome_rating,
            outcome_notes=outcome_notes
        )
        
        if success:
            return jsonify({'success': True, 'message': 'Outcome feedback recorded successfully'})
        else:
            return jsonify({'error': 'Failed to record outcome feedback'}), 500
            
    except Exception as e:
        logger.error(f"Error submitting outcome feedback: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@ml_bp.route('/ml/feedback-analytics')
@login_required
def feedback_analytics():
    """View feedback analytics and learning insights"""
    try:
        if not current_user.has_role('admin'):
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('enhanced_review.period_analysis_v2'))
        
        days = request.args.get('days', 30, type=int)
        
        feedback_service = MLFeedbackService()
        analytics = feedback_service.get_feedback_analytics(days=days)
        insights = feedback_service.get_learning_insights()
        should_retrain, retrain_reason = feedback_service.should_retrain_models()
        
        return render_template('ml/feedback_analytics.html',
                             analytics=analytics,
                             insights=insights,
                             should_retrain=should_retrain,
                             retrain_reason=retrain_reason,
                             days=days)
        
    except Exception as e:
        logger.error(f"Error loading feedback analytics: {str(e)}")
        flash('Error loading feedback analytics', 'error')
        return redirect(url_for('ml.ml_dashboard'))

@ml_bp.route('/ml/user-preferences')
@login_required
def user_preferences():
    """Manage user recommendation preferences"""
    try:
        feedback_service = MLFeedbackService()
        preferences = feedback_service.get_user_preferences(current_user.id)
        
        return render_template('ml/user_preferences.html', preferences=preferences)
        
    except Exception as e:
        logger.error(f"Error loading user preferences: {str(e)}")
        flash('Error loading preferences', 'error')
        return redirect(url_for('enhanced_review.period_analysis_v2'))

@ml_bp.route('/api/ml/user-preferences', methods=['POST'])
@login_required
def update_user_preferences():
    """Update user recommendation preferences"""
    try:
        data = request.get_json()
        
        feedback_service = MLFeedbackService()
        success = feedback_service.update_user_preferences(current_user.id, data)
        
        if success:
            return jsonify({'success': True, 'message': 'Preferences updated successfully'})
        else:
            return jsonify({'error': 'Failed to update preferences'}), 500
            
    except Exception as e:
        logger.error(f"Error updating user preferences: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@ml_bp.route('/api/ml/learning-insights')
@login_required
def get_learning_insights():
    """Get learning insights for model improvement"""
    try:
        if not current_user.has_role('admin'):
            return jsonify({'error': 'Access denied'}), 403
        
        feedback_service = MLFeedbackService()
        insights = feedback_service.get_learning_insights()
        
        return jsonify({
            'success': True,
            'insights': insights
        })
        
    except Exception as e:
        logger.error(f"Error getting learning insights: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@ml_bp.route('/ml/natural-language-feedback')
@login_required
def natural_language_feedback():
    """Natural language feedback form for AI model improvement"""
    return render_template('ml/natural_language_feedback.html')

@ml_bp.route('/api/ml/natural-language-feedback', methods=['POST'])
@login_required
def submit_natural_language_feedback():
    """Submit natural language feedback for AI model improvement"""
    try:
        data = request.get_json()
        feedback_text = data.get('feedback_text', '').strip()
        review_id = data.get('review_id')
        context = data.get('context', {})
        
        if not feedback_text:
            return jsonify({
                'success': False,
                'error': 'Feedback text is required'
            }), 400
        
        # Add user context
        context['user_id'] = current_user.id
        if review_id:
            context['review_id'] = review_id
        
        # Process the natural language feedback
        feedback_service = MLFeedbackService()
        insights = feedback_service.process_natural_language_feedback(feedback_text, context)
        
        if 'error' in insights:
            return jsonify({
                'success': False,
                'error': insights['error']
            }), 500
        
        return jsonify({
            'success': True,
            'message': 'Feedback processed successfully',
            'insights': insights,
            'learning_summary': insights.get('learning_summary', '')
        })
        
    except Exception as e:
        logger.error(f"Error processing natural language feedback: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@ml_bp.route('/api/ml/feedback-learning-insights')
@login_required
def get_feedback_learning_insights():
    """Get insights from natural language feedback processing"""
    try:
        if not current_user.has_role('admin'):
            return jsonify({'error': 'Access denied'}), 403
        
        feedback_service = MLFeedbackService()
        insights = feedback_service.get_feedback_learning_insights()
        
        return jsonify({
            'success': True,
            'insights': insights
        })
        
    except Exception as e:
        logger.error(f"Error getting feedback learning insights: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


