#!/usr/bin/env python3
"""
Example of how the behavioral ML system would work in practice
"""

from behavioral_prediction_engine import BehavioralPredictionEngine

def demonstrate_behavioral_predictions():
    """Demonstrate how the behavioral ML system predicts user modifications"""
    
    # Initialize the behavioral prediction engine
    predictor = BehavioralPredictionEngine()
    
    # Example recommendations that a user might receive
    sample_recommendations = [
        {
            'id': 1,
            'security_id': 101,
            'symbol': 'RELIANCE',
            'action': 'BUY',
            'quantity': 100,
            'amount': 25000,
            'current_weight': 2.5,
            'target_weight': 5.0,
            'total_trades': 8,
            'number_of_securities': 5,
            'total_amount': 200000,
            'stock_outlook_score': 75,
            'sector_outlook_score': 70,
            'price_vs_fair_value_ratio': 0.95,
            'price_momentum_score': 65,
            'valuation_attractiveness': 80,
            'concentration_risk': 25,
            'sector_diversification_score': 60,
            'stock_diversification_score': 55
        },
        {
            'id': 2,
            'security_id': 102,
            'symbol': 'TCS',
            'action': 'SELL',
            'quantity': 50,
            'amount': 18000,
            'current_weight': 8.0,
            'target_weight': 3.0,
            'total_trades': 8,
            'number_of_securities': 5,
            'total_amount': 200000,
            'stock_outlook_score': 60,
            'sector_outlook_score': 65,
            'price_vs_fair_value_ratio': 1.05,
            'price_momentum_score': 55,
            'valuation_attractiveness': 45,
            'concentration_risk': 25,
            'sector_diversification_score': 60,
            'stock_diversification_score': 55
        },
        {
            'id': 3,
            'security_id': 103,
            'symbol': 'HDFC',
            'action': 'BUY',
            'quantity': 25,
            'amount': 4500,
            'current_weight': 1.0,
            'target_weight': 2.5,
            'total_trades': 8,
            'number_of_securities': 5,
            'total_amount': 200000,
            'stock_outlook_score': 85,
            'sector_outlook_score': 80,
            'price_vs_fair_value_ratio': 0.88,
            'price_momentum_score': 75,
            'valuation_attractiveness': 90,
            'concentration_risk': 25,
            'sector_diversification_score': 60,
            'stock_diversification_score': 55
        }
    ]
    
    # Get behavioral predictions
    predictions = predictor.predict_user_modifications(client_id=123, recommendations=sample_recommendations)
    
    print("🧠 Behavioral ML Prediction Results")
    print("=" * 60)
    
    for pred in predictions:
        print(f"\n📊 {pred['symbol']} ({pred['original_action']})")
        print(f"   Original: {pred['original_quantity']} shares @ ₹{pred['original_amount']:,}")
        
        # Show behavioral scores
        scores = pred['predicted_modifications']
        print(f"   🎯 Behavioral Analysis:")
        print(f"      • Trade Minimization Impact: {scores['trade_minimization_impact']:.1%}")
        print(f"      • Sell Avoidance Likelihood: {scores['sell_avoidance_likelihood']:.1%}")
        print(f"      • Buy Preference Strength: {scores['buy_preference_strength']:.1%}")
        print(f"      • Price Sensitivity Score: {scores['price_sensitivity_score']:.1%}")
        
        # Show predictions
        print(f"   🔮 Predicted Changes:")
        print(f"      • Predicted Quantity: {pred['predicted_quantity']} shares")
        print(f"      • Predicted Amount: ₹{pred['predicted_amount']:,.0f}")
        print(f"      • Predicted Action: {pred['predicted_action']}")
        print(f"      • Confidence: {pred['modification_confidence']}%")
        
        # Show suggestions
        if pred['suggested_actions']:
            print(f"   💡 Suggestions:")
            for suggestion in pred['suggested_actions']:
                print(f"      • {suggestion}")

if __name__ == "__main__":
    demonstrate_behavioral_predictions()
