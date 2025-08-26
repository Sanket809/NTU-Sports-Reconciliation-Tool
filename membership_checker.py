import pandas as pd
import difflib
from datetime import datetime
import os

# Constants
ANNUAL_FEE = 120.0
HOURLY_RATE = 5.0
FUZZY_CUTOFF = 0.86

def normalize_name(name):
    """Normalize names for consistent comparison"""
    if pd.isna(name):
        return ""
    return str(name).lower().strip().replace("  ", " ")

def load_and_preprocess_data():
    """Load and preprocess all input CSVs"""
    # Load data
    members_df = pd.read_csv('members.csv')
    payments_df = pd.read_csv('membership_payments.csv')
    external_df = pd.read_csv('external_bookings.csv')
    
    # Normalize names
    members_df['NormalizedName'] = members_df['FullName'].apply(normalize_name)
    payments_df['NormalizedName'] = payments_df['FullName'].apply(normalize_name)
    external_df['NormalizedName'] = external_df['FullName'].apply(normalize_name)
    
    return members_df, payments_df, external_df

def reconcile_memberships(members_df, payments_df):
    """Reconcile membership payments with selected players"""
    # Get selected players
    selected_players = members_df[members_df['IsSelectedOfficialTeam'] == 'Yes'].copy()
    
    # Initialize result columns
    selected_players['PaidAmount'] = 0.0
    selected_players['PaidStatus'] = 'Unpaid'
    selected_players['Outstanding'] = ANNUAL_FEE
    selected_players['PaymentDate'] = None
    
    # Track matched payments
    matched_payment_indices = set()
    fuzzy_suggestions = []
    resolved_payments = []
    
    # First pass: match by StudentID
    for idx, payment in payments_df.iterrows():
        resolved_payment = payment.to_dict()
        resolved_payment['ResolvedStudentID'] = None
        resolved_payment['MatchType'] = 'Unmatched'
        
        if not pd.isna(payment.get('StudentID')):
            student_id = payment['StudentID']
            match = selected_players[selected_players['StudentID'] == student_id]
            if not match.empty:
                matched_idx = match.index[0]
                selected_players.at[matched_idx, 'PaidAmount'] += payment['Amount']
                selected_players.at[matched_idx, 'Outstanding'] = max(0, ANNUAL_FEE - selected_players.at[matched_idx, 'PaidAmount'])
                selected_players.at[matched_idx, 'PaymentDate'] = payment['PaymentDate']
                matched_payment_indices.add(idx)
                resolved_payment['ResolvedStudentID'] = selected_players.at[matched_idx, 'StudentID']
                resolved_payment['MatchType'] = 'StudentID'
        
        resolved_payments.append(resolved_payment)
    
    # Second pass: fuzzy match by name for unmatched payments
    all_selected_names = selected_players['NormalizedName'].tolist()
    
    for idx, payment in payments_df.iterrows():
        if idx in matched_payment_indices:
            continue
            
        normalized_payment_name = normalize_name(payment['FullName'])
        if not normalized_payment_name:
            continue
            
        # Fuzzy match
        matches = difflib.get_close_matches(
            normalized_payment_name, 
            all_selected_names, 
            n=1, 
            cutoff=FUZZY_CUTOFF
        )
        
        if matches:
            matched_name = matches[0]
            match = selected_players[selected_players['NormalizedName'] == matched_name]
            if not match.empty:
                matched_idx = match.index[0]
                selected_players.at[matched_idx, 'PaidAmount'] += payment['Amount']
                selected_players.at[matched_idx, 'Outstanding'] = max(0, ANNUAL_FEE - selected_players.at[matched_idx, 'PaidAmount'])
                selected_players.at[matched_idx, 'PaymentDate'] = payment['PaymentDate']
                matched_payment_indices.add(idx)
                
                # Update resolved payment
                for rp in resolved_payments:
                    if rp['NormalizedName'] == normalized_payment_name and rp['MatchType'] == 'Unmatched':
                        rp['ResolvedStudentID'] = selected_players.at[matched_idx, 'StudentID']
                        rp['MatchType'] = 'FuzzyName'
                
                # Add to suggestions
                if normalized_payment_name != matched_name:
                    fuzzy_suggestions.append({
                        'EnteredName': payment['FullName'],
                        'SuggestedName': selected_players.at[matched_idx, 'FullName']
                    })
    
    # Update payment status
    for idx, player in selected_players.iterrows():
        if player['PaidAmount'] >= ANNUAL_FEE:
            selected_players.at[idx, 'PaidStatus'] = 'Paid'
        elif player['PaidAmount'] > 0:
            selected_players.at[idx, 'PaidStatus'] = 'Underpaid'
        else:
            selected_players.at[idx, 'PaidStatus'] = 'Unpaid'
    
    # Find payments from non-selected players
    all_member_ids = set(members_df['StudentID'])
    paid_not_selected = []
    
    for idx, payment in payments_df.iterrows():
        if idx in matched_payment_indices:
            continue
            
        # Check if this payment is from any member (selected or not)
        payment_matched = False
        if not pd.isna(payment.get('StudentID')):
            if payment['StudentID'] in all_member_ids:
                payment_matched = True
        
        if not payment_matched and not pd.isna(payment.get('FullName')):
            payment_name = normalize_name(payment['FullName'])
            if payment_name in members_df['NormalizedName'].values:
                payment_matched = True
        
        if not payment_matched:
            paid_not_selected.append(payment.to_dict())
    
    # Find completely unmatched payments
    unmatched_payments = []
    for rp in resolved_payments:
        if rp['MatchType'] == 'Unmatched':
            unmatched_payments.append(rp)
    
    return selected_players, fuzzy_suggestions, paid_not_selected, unmatched_payments, resolved_payments

def validate_external_bookings(external_df):
    """Validate external bookings against hourly rate"""
    external_df = external_df.copy()
    external_df['Expected'] = external_df['Hours'] * HOURLY_RATE
    external_df['Underpaid'] = external_df['AmountPaid'] < external_df['Expected'] - 0.01
    external_df['MissingPayment'] = external_df['AmountPaid'] <= 0
    
    # Identify problematic bookings
    external_issues = external_df[
        (external_df['Underpaid']) | 
        (external_df['MissingPayment'])
    ].copy()
    
    return external_df, external_issues

def generate_summary(selected_players, paid_not_selected, unmatched_payments, external_df, external_issues):
    """Generate summary statistics"""
    total_selected = len(selected_players)
    paid_count = len(selected_players[selected_players['PaidStatus'] == 'Paid'])
    underpaid_count = len(selected_players[selected_players['PaidStatus'] == 'Underpaid'])
    unpaid_count = len(selected_players[selected_players['PaidStatus'] == 'Unpaid'])
    
    mismatch_rate = (underpaid_count + unpaid_count) / total_selected * 100 if total_selected > 0 else 0
    
    membership_expected = total_selected * ANNUAL_FEE
    membership_collected = selected_players['PaidAmount'].sum()
    
    external_expected = external_df['Expected'].sum()
    external_collected = external_df['AmountPaid'].sum()
    external_issues_count = len(external_issues)
    
    non_selected_payments_count = len(paid_not_selected)
    unmatched_payments_count = len(unmatched_payments)
    
    summary = f"""NTU Sports - Membership & Bookings Reconciliation
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

MEMBERSHIP SUMMARY:
Total selected members: {total_selected}
- Paid in full: {paid_count}
- Underpaid: {underpaid_count}
- Unpaid: {unpaid_count}
Mismatch rate: {mismatch_rate:.1f}%

Membership revenue:
- Expected: £{membership_expected:,.2f}
- Collected: £{membership_collected:,.2f}
- Difference: £{membership_collected - membership_expected:,.2f}

EXTERNAL BOOKINGS:
Total bookings: {len(external_df)}
- Expected: £{external_expected:,.2f}
- Collected: £{external_collected:,.2f}
- Difference: £{external_collected - external_expected:,.2f}
- Bookings with issues: {external_issues_count}

ADDITIONAL FINDINGS:
- Payments from non-selected players: {non_selected_payments_count}
- Unmatched payments (need review): {unmatched_payments_count}
"""
    return summary

def save_outputs(selected_players, fuzzy_suggestions, paid_not_selected, 
                 unmatched_payments, resolved_payments, external_df, external_issues, summary):
    """Save all output files"""
    # Save summary
    with open('ntu_membership_summary.txt', 'w') as f:
        f.write(summary)
    
    # Save CSV files
    selected_players[['StudentID', 'FullName', 'Team', 'PaidAmount', 'PaidStatus', 'Outstanding', 'PaymentDate']].to_csv(
        'ntu_membership_selected_status.csv', index=False)
    
    pd.DataFrame(paid_not_selected).to_csv('ntu_membership_paid_not_selected.csv', index=False)
    pd.DataFrame(unmatched_payments).to_csv('ntu_membership_unmatched_payments.csv', index=False)
    pd.DataFrame(fuzzy_suggestions).to_csv('ntu_membership_fuzzy_suggestions.csv', index=False)
    
    resolved_df = pd.DataFrame(resolved_payments)
    if 'NormalizedName' in resolved_df.columns:
        resolved_df = resolved_df.drop(columns=['NormalizedName'])
    resolved_df.to_csv('ntu_membership_payments_resolved.csv', index=False)
    
    external_df.to_csv('ntu_membership_external_all.csv', index=False)
    external_issues.to_csv('ntu_membership_external_issues.csv', index=False)

def main():
    """Main function"""
    print("NTU Sports - Membership & Bookings Reconciliation Tool")
    print("Loading data...")
    
    try:
        # Load and preprocess data
        members_df, payments_df, external_df = load_and_preprocess_data()
        
        print("Reconciling memberships...")
        selected_players, fuzzy_suggestions, paid_not_selected, unmatched_payments, resolved_payments = reconcile_memberships(
            members_df, payments_df)
        
        print("Validating external bookings...")
        external_df, external_issues = validate_external_bookings(external_df)
        
        print("Generating reports...")
        summary = generate_summary(
            selected_players, paid_not_selected, unmatched_payments, external_df, external_issues)
        
        # Save outputs
        save_outputs(
            selected_players, fuzzy_suggestions, paid_not_selected, 
            unmatched_payments, resolved_payments, external_df, external_issues, summary)
        
        # Print summary to console
        print("\n" + summary)
        print("Reconciliation complete! All output files have been generated.")
        
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        raise

if __name__ == "__main__":
    main()