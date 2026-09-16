#!/usr/bin/env python3
"""
Simple example of behavioral ML predictions without external dependencies
"""

def demonstrate_behavioral_predictions():
    """Demonstrate how the behavioral ML system would predict user modifications"""
    
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
    
    print("🧠 Behavioral ML Prediction Results")
    print("=" * 60)
    
    for rec in sample_recommendations:
        print(f"\n📊 {rec['symbol']} ({rec['action']})")
        print(f"   Original: {rec['quantity']} shares @ ₹{rec['amount']:,}")
        
        # Calculate behavioral scores
        current_weight = rec['current_weight']
        target_weight = rec['target_weight']
        action = rec['action']
        
        # 1. Trade Minimization Analysis
        total_trades = rec['total_trades']
        avg_trade_size = rec['total_amount'] / max(total_trades, 1)
        trade_complexity = "HIGH" if total_trades > 5 or avg_trade_size < 10000 else "MEDIUM" if total_trades > 3 else "LOW"
        
        # 2. Sell Avoidance Analysis
        sell_avoidance = "HIGH" if action == 'SELL' and current_weight > target_weight and (current_weight - target_weight) > 2.0 else "MEDIUM" if action == 'SELL' and current_weight > target_weight else "LOW"
        
        # 3. Buy Preference Analysis
        underweight = max(0, target_weight - current_weight)
        avg_outlook = (rec['stock_outlook_score'] + rec['sector_outlook_score']) / 2
        buy_preference = "HIGH" if action == 'BUY' and underweight > 2.0 and avg_outlook > 70 else "MEDIUM" if action == 'BUY' and underweight > 1.0 and avg_outlook > 60 else "LOW"
        
        # 4. Price Sensitivity Analysis
        price_attractiveness = "GOOD" if rec['price_vs_fair_value_ratio'] < 0.9 and rec['valuation_attractiveness'] > 70 else "POOR" if rec['price_vs_fair_value_ratio'] > 1.1 or rec['valuation_attractiveness'] < 50 else "FAIR"
        
        print(f"   🎯 Behavioral Analysis:")
        print(f"      • Trade Complexity: {trade_complexity} ({total_trades} trades, ₹{avg_trade_size:,.0f} avg)")
        print(f"      • Sell Avoidance Risk: {sell_avoidance}")
        print(f"      • Buy Preference: {buy_preference}")
        print(f"      • Price Attractiveness: {price_attractiveness}")
        
        # Predict modifications
        original_qty = rec['quantity']
        original_amt = rec['amount']
        
        # Trade minimization: reduce quantity
        if trade_complexity == "HIGH":
            predicted_qty = int(original_qty * 0.8)  # 20% reduction
            predicted_amt = original_amt * 0.8
        elif trade_complexity == "MEDIUM":
            predicted_qty = int(original_qty * 0.9)  # 10% reduction
            predicted_amt = original_amt * 0.9
        else:
            predicted_qty = original_qty
            predicted_amt = original_amt
        
        # Sell avoidance: further reduce if SELL
        if sell_avoidance == "HIGH":
            predicted_qty = int(predicted_qty * 0.5)  # 50% reduction for high sell avoidance
            predicted_amt = predicted_amt * 0.5
            predicted_action = "HOLD"  # Change SELL to HOLD
        elif sell_avoidance == "MEDIUM":
            predicted_qty = int(predicted_qty * 0.7)  # 30% reduction
            predicted_amt = predicted_amt * 0.7
            predicted_action = action  # Keep original action
        else:
            predicted_action = action
        
        # Price sensitivity: reduce if poor price
        if price_attractiveness == "POOR":
            predicted_qty = int(predicted_qty * 0.6)  # 40% reduction for poor price
            predicted_amt = predicted_amt * 0.6
        
        # Buy preference: increase if high preference
        if buy_preference == "HIGH":
            predicted_qty = int(predicted_qty * 1.2)  # 20% increase
            predicted_amt = predicted_amt * 1.2
        
        print(f"   🔮 Predicted Changes:")
        print(f"      • Predicted Quantity: {predicted_qty} shares (was {original_qty})")
        print(f"      • Predicted Amount: ₹{predicted_amt:,.0f} (was ₹{original_amt:,.0f})")
        print(f"      • Predicted Action: {predicted_action}")
        
        # Calculate confidence
        confidence = 85 if trade_complexity == "HIGH" or sell_avoidance == "HIGH" else 70 if trade_complexity == "MEDIUM" or sell_avoidance == "MEDIUM" else 55
        print(f"      • Confidence: {confidence}%")
        
        # Generate suggestions
        suggestions = []
        if trade_complexity == "HIGH":
            suggestions.append("Consider consolidating trades to minimize complexity")
        if sell_avoidance in ["HIGH", "MEDIUM"]:
            suggestions.append("User typically avoids selling overweight positions")
        if buy_preference == "HIGH":
            suggestions.append("Strong buy preference - user may increase quantity")
        if price_attractiveness == "POOR":
            suggestions.append("Poor price attractiveness - user may reduce quantity")
        
        if suggestions:
            print(f"   💡 Suggestions:")
            for suggestion in suggestions:
                print(f"      • {suggestion}")

if __name__ == "__main__":
    demonstrate_behavioral_predictions()
