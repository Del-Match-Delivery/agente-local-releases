//! Receipt formatting for ESC/POS printers

use super::commands::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReceiptContent {
    pub restaurant_name: String,
    pub restaurant_address: Option<String>,
    pub restaurant_phone: Option<String>,
    pub order_number: Option<String>,
    pub pickup_code: Option<String>,
    pub customer_name: Option<String>,
    pub items: Vec<ReceiptItem>,
    pub subtotal_cents: i64,
    pub discount_cents: i64,
    pub delivery_fee_cents: i64,
    pub total_cents: i64,
    pub payment_method: Option<String>,
    pub notes: Option<String>,
    pub order_type: Option<String>, // delivery, pickup, dine_in
    pub delivery_address: Option<String>,
    pub table_number: Option<String>,
    pub created_at: String,
    /// When true, prints each item's product code as an EAN-13 barcode
    /// instead of plain text (falls back to text if the code isn't a
    /// valid EAN-13). Defaults to false so agents receiving a payload
    /// without this field keep printing exactly as before.
    #[serde(default)]
    pub print_barcode_ean13: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReceiptItem {
    pub name: String,
    pub quantity: i32,
    pub unit_price_cents: i64,
    pub total_cents: i64,
    pub notes: Option<String>,
    pub addons: Vec<String>,
    pub barcode: Option<String>,
}

pub struct ReceiptFormatter {
    paper_width: usize,
}

impl ReceiptFormatter {
    pub fn new(paper_width_mm: u8) -> Self {
        let chars = if paper_width_mm >= 80 { 48 } else { 32 };
        Self { paper_width: chars }
    }
    
    pub fn format_customer_receipt(&self, content: &ReceiptContent) -> Vec<u8> {
        let mut bytes = Vec::new();
        let width = self.paper_width;
        
        // Initialize
        bytes.extend(initialize());
        bytes.extend(select_brazilian());
        
        // Header
        bytes.extend(align_center());
        bytes.extend(set_font_size(2, 2));
        bytes.extend(set_bold(true));
        bytes.extend(print_text(&content.restaurant_name));
        bytes.extend(line_feed());
        
        bytes.extend(set_font_size(1, 1));
        bytes.extend(set_bold(false));
        
        if let Some(ref addr) = content.restaurant_address {
            bytes.extend(print_text(addr));
            bytes.extend(line_feed());
        }
        
        if let Some(ref phone) = content.restaurant_phone {
            bytes.extend(print_text(&format!("Tel: {}", phone)));
            bytes.extend(line_feed());
        }
        
        bytes.extend(line_feed());
        
        // Pickup code (big and bold)
        if let Some(ref code) = content.pickup_code {
            bytes.extend(set_font_size(3, 3));
            bytes.extend(set_bold(true));
            bytes.extend(print_text(&format!("SENHA: {}", code)));
            bytes.extend(line_feed());
            bytes.extend(set_font_size(1, 1));
            bytes.extend(set_bold(false));
        }
        
        // Order info
        bytes.extend(align_left());
        bytes.extend(separator(width));
        
        if let Some(ref order_num) = content.order_number {
            bytes.extend(print_text(&format!("Pedido: #{}", order_num)));
            bytes.extend(line_feed());
        }
        
        bytes.extend(print_text(&format!("Data: {}", &content.created_at[..19].replace("T", " "))));
        bytes.extend(line_feed());
        
        if let Some(ref order_type) = content.order_type {
            let tipo = match order_type.as_str() {
                "delivery" => "Entrega",
                "pickup" => "Retirada",
                "dine_in" => "Local",
                _ => order_type,
            };
            bytes.extend(print_text(&format!("Tipo: {}", tipo)));
            bytes.extend(line_feed());
        }
        
        if let Some(ref customer) = content.customer_name {
            bytes.extend(print_text(&format!("Cliente: {}", customer)));
            bytes.extend(line_feed());
        }
        
        if let Some(ref table) = content.table_number {
            bytes.extend(set_bold(true));
            bytes.extend(print_text(&format!("Mesa: {}", table)));
            bytes.extend(line_feed());
            bytes.extend(set_bold(false));
        }
        
        // Items
        bytes.extend(double_separator(width));
        bytes.extend(set_bold(true));
        bytes.extend(print_text("ITENS"));
        bytes.extend(line_feed());
        bytes.extend(set_bold(false));
        bytes.extend(separator(width));
        
        for item in &content.items {
            // Item line: qty x name
            let item_line = format!("{}x {}", item.quantity, item.name);
            bytes.extend(print_text(&item_line));
            bytes.extend(line_feed());
            
            // Price on right
            let price_str = format_currency(item.total_cents);
            let padding = width.saturating_sub(price_str.len());
            bytes.extend(std::iter::repeat(b' ').take(padding));
            bytes.extend(print_text(&price_str));
            bytes.extend(line_feed());

            // Código do produto (barcode/EAN), quando cadastrado
            if let Some(ref barcode) = item.barcode {
                if !barcode.is_empty() {
                    if content.print_barcode_ean13 && is_valid_ean13(barcode) {
                        bytes.extend(print_barcode_ean13(barcode, 60));
                    } else {
                        bytes.extend(print_text(&format!("  Cod: {}", barcode)));
                        bytes.extend(line_feed());
                    }
                }
            }

            // Addons
            for addon in &item.addons {
                bytes.extend(print_text(&format!("  + {}", addon)));
                bytes.extend(line_feed());
            }
            
            // Notes
            if let Some(ref notes) = item.notes {
                bytes.extend(print_text(&format!("  Obs: {}", notes)));
                bytes.extend(line_feed());
            }
        }
        
        // Totals
        bytes.extend(separator(width));
        
        bytes.extend(self.format_money_line("Subtotal:", content.subtotal_cents));
        
        if content.discount_cents > 0 {
            bytes.extend(self.format_money_line("Desconto:", -content.discount_cents));
        }
        
        if content.delivery_fee_cents > 0 {
            bytes.extend(self.format_money_line("Taxa entrega:", content.delivery_fee_cents));
        }
        
        bytes.extend(double_separator(width));
        bytes.extend(set_font_size(2, 2));
        bytes.extend(set_bold(true));
        bytes.extend(self.format_money_line("TOTAL:", content.total_cents));
        bytes.extend(set_font_size(1, 1));
        bytes.extend(set_bold(false));
        
        // Payment
        if let Some(ref payment) = content.payment_method {
            bytes.extend(line_feed());
            bytes.extend(print_text(&format!("Pagamento: {}", payment)));
            bytes.extend(line_feed());
        }
        
        // Delivery address
        if let Some(ref addr) = content.delivery_address {
            bytes.extend(separator(width));
            bytes.extend(set_bold(true));
            bytes.extend(print_text("ENDEREÇO DE ENTREGA:"));
            bytes.extend(line_feed());
            bytes.extend(set_bold(false));
            bytes.extend(print_text(addr));
            bytes.extend(line_feed());
        }
        
        // Notes
        if let Some(ref notes) = content.notes {
            bytes.extend(separator(width));
            bytes.extend(print_text(&format!("Obs: {}", notes)));
            bytes.extend(line_feed());
        }
        
        // Footer
        bytes.extend(line_feed());
        bytes.extend(align_center());
        bytes.extend(print_text("Obrigado pela preferência!"));
        bytes.extend(line_feed());
        bytes.extend(print_text("miacardapio.com.br"));
        bytes.extend(line_feed());
        
        // Feed and cut
        bytes.extend(feed_lines(4));
        bytes.extend(cut_paper());
        
        bytes
    }
    
    /// Format a sector comanda (kitchen, bar, etc.) with a custom title header.
    pub fn format_sector_receipt(&self, content: &ReceiptContent, sector_title: &str) -> Vec<u8> {
        let mut bytes = Vec::new();
        let width = self.paper_width;
        
        bytes.extend(initialize());
        bytes.extend(select_brazilian());
        
        // Sector title header
        bytes.extend(align_center());
        bytes.extend(set_font_size(3, 3));
        bytes.extend(set_bold(true));
        bytes.extend(print_text(sector_title));
        bytes.extend(line_feed());
        bytes.extend(set_bold(false));
        
        // Big pickup code
        if let Some(ref code) = content.pickup_code {
            bytes.extend(set_font_size(4, 4));
            bytes.extend(set_bold(true));
            bytes.extend(print_text(code));
            bytes.extend(line_feed());
        }
        
        bytes.extend(set_font_size(2, 2));
        if let Some(ref order_type) = content.order_type {
            let tipo = match order_type.as_str() {
                "delivery" => "🚗 ENTREGA",
                "pickup" => "🏃 RETIRADA",
                "dine_in" => "🍽️ LOCAL",
                _ => order_type,
            };
            bytes.extend(print_text(tipo));
            bytes.extend(line_feed());
        }
        
        if let Some(ref table) = content.table_number {
            bytes.extend(print_text(&format!("MESA {}", table)));
            bytes.extend(line_feed());
        }
        
        bytes.extend(set_font_size(1, 1));
        bytes.extend(set_bold(false));
        bytes.extend(align_left());
        
        // Time
        bytes.extend(print_text(&format!("Hora: {}", &content.created_at[11..16])));
        bytes.extend(line_feed());
        
        if let Some(ref customer) = content.customer_name {
            bytes.extend(print_text(&format!("Cliente: {}", customer)));
            bytes.extend(line_feed());
        }
        
        bytes.extend(double_separator(width));
        
        // Items (bigger font for kitchen)
        for item in &content.items {
            bytes.extend(set_font_size(2, 2));
            bytes.extend(set_bold(true));
            bytes.extend(print_text(&format!("{}x {}", item.quantity, item.name)));
            bytes.extend(line_feed());
            bytes.extend(set_font_size(1, 1));
            bytes.extend(set_bold(false));
            
            for addon in &item.addons {
                bytes.extend(print_text(&format!("   + {}", addon)));
                bytes.extend(line_feed());
            }
            
            if let Some(ref notes) = item.notes {
                bytes.extend(set_bold(true));
                bytes.extend(print_text(&format!("   *** {} ***", notes)));
                bytes.extend(line_feed());
                bytes.extend(set_bold(false));
            }
            
            bytes.extend(line_feed());
        }
        
        // General notes
        if let Some(ref notes) = content.notes {
            bytes.extend(separator(width));
            bytes.extend(set_bold(true));
            bytes.extend(print_text(&format!("OBS: {}", notes)));
            bytes.extend(line_feed());
            bytes.extend(set_bold(false));
        }
        
        bytes.extend(feed_lines(3));
        bytes.extend(cut_paper());
        
        bytes
    }
    
    fn format_money_line(&self, label: &str, cents: i64) -> Vec<u8> {
        let mut bytes = Vec::new();
        let value = format_currency(cents);
        let total_len = label.len() + value.len();
        let padding = self.paper_width.saturating_sub(total_len);
        
        bytes.extend(print_text(label));
        bytes.extend(std::iter::repeat(b' ').take(padding));
        bytes.extend(print_text(&value));
        bytes.extend(line_feed());
        
        bytes
    }
}

fn format_currency(cents: i64) -> String {
    let abs_cents = cents.abs();
    let sign = if cents < 0 { "-" } else { "" };
    format!("{}R${},{:02}", sign, abs_cents / 100, abs_cents % 100)
}

/// Format a test page
pub fn format_test_page() -> Vec<u8> {
    let mut bytes = Vec::new();
    
    bytes.extend(initialize());
    bytes.extend(select_brazilian());
    
    bytes.extend(align_center());
    bytes.extend(set_font_size(2, 2));
    bytes.extend(set_bold(true));
    bytes.extend(print_text("TESTE DE IMPRESSÃO"));
    bytes.extend(line_feed());
    
    bytes.extend(set_font_size(1, 1));
    bytes.extend(set_bold(false));
    bytes.extend(print_text("MiaCardapio Agent"));
    bytes.extend(line_feed());
    bytes.extend(line_feed());
    
    bytes.extend(align_left());
    bytes.extend(print_text("Caracteres especiais:"));
    bytes.extend(line_feed());
    bytes.extend(print_text("áéíóúàèìòùãõâêîôûç"));
    bytes.extend(line_feed());
    bytes.extend(print_text("ÁÉÍÓÚÀÈÌÒÙÃÕÂÊÎÔÛÇ"));
    bytes.extend(line_feed());
    bytes.extend(line_feed());
    
    bytes.extend(print_text("Tamanhos de fonte:"));
    bytes.extend(line_feed());
    bytes.extend(set_font_size(1, 1));
    bytes.extend(print_text("Normal (1x1)"));
    bytes.extend(line_feed());
    bytes.extend(set_font_size(2, 1));
    bytes.extend(print_text("Largo (2x1)"));
    bytes.extend(line_feed());
    bytes.extend(set_font_size(1, 2));
    bytes.extend(print_text("Alto (1x2)"));
    bytes.extend(line_feed());
    bytes.extend(set_font_size(2, 2));
    bytes.extend(print_text("Grande (2x2)"));
    bytes.extend(line_feed());
    bytes.extend(set_font_size(1, 1));
    bytes.extend(line_feed());
    
    bytes.extend(print_text("Estilos:"));
    bytes.extend(line_feed());
    bytes.extend(set_bold(true));
    bytes.extend(print_text("Negrito"));
    bytes.extend(set_bold(false));
    bytes.extend(line_feed());
    bytes.extend(set_underline(1));
    bytes.extend(print_text("Sublinhado"));
    bytes.extend(set_underline(0));
    bytes.extend(line_feed());
    bytes.extend(set_inverted(true));
    bytes.extend(print_text(" Invertido "));
    bytes.extend(set_inverted(false));
    bytes.extend(line_feed());
    bytes.extend(line_feed());
    
    bytes.extend(align_center());
    bytes.extend(print_text("Impressora OK!"));
    bytes.extend(line_feed());
    
    let now = chrono::Local::now();
    bytes.extend(print_text(&now.format("%d/%m/%Y %H:%M:%S").to_string()));
    bytes.extend(line_feed());
    
    bytes.extend(feed_lines(4));
    bytes.extend(cut_paper());

    bytes
}

#[cfg(test)]
mod tests {
    use super::*;

    const VALID_EAN13: &str = "4006381333931";

    // Base payload shaped like what the backend sends today, i.e. with NO
    // `print_barcode_ean13` key at all (simulates a store/agent that predates
    // this feature).
    fn legacy_payload_json(barcode: &str) -> serde_json::Value {
        serde_json::json!({
            "restaurant_name": "Loja Teste",
            "items": [{
                "name": "Produto",
                "quantity": 1,
                "unit_price_cents": 1000,
                "total_cents": 1000,
                "addons": [],
                "barcode": barcode
            }],
            "subtotal_cents": 1000,
            "discount_cents": 0,
            "delivery_fee_cents": 0,
            "total_cents": 1000,
            "created_at": "2026-09-25T12:00:00Z"
        })
    }

    #[test]
    fn print_barcode_ean13_field_defaults_to_false_without_it_in_payload() {
        let content: ReceiptContent =
            serde_json::from_value(legacy_payload_json(VALID_EAN13)).unwrap();
        assert_eq!(content.print_barcode_ean13, false);
    }

    #[test]
    fn legacy_payload_keeps_printing_product_code_as_text() {
        let content: ReceiptContent =
            serde_json::from_value(legacy_payload_json(VALID_EAN13)).unwrap();
        let formatter = ReceiptFormatter::new(80);
        let bytes = formatter.format_customer_receipt(&content);
        let text = String::from_utf8_lossy(&bytes);
        assert!(text.contains(&format!("Cod: {}", VALID_EAN13)));
    }

    #[test]
    fn flag_enabled_with_valid_ean13_prints_barcode_instead_of_text() {
        let mut json = legacy_payload_json(VALID_EAN13);
        json["print_barcode_ean13"] = serde_json::json!(true);
        let content: ReceiptContent = serde_json::from_value(json).unwrap();
        let formatter = ReceiptFormatter::new(80);
        let bytes = formatter.format_customer_receipt(&content);
        let text = String::from_utf8_lossy(&bytes);
        assert!(!text.contains("Cod:"));
        assert!(bytes
            .windows(VALID_EAN13.len())
            .any(|w| w == VALID_EAN13.as_bytes()));
    }

    #[test]
    fn flag_enabled_with_invalid_ean13_falls_back_to_text() {
        let mut json = legacy_payload_json("12345");
        json["print_barcode_ean13"] = serde_json::json!(true);
        let content: ReceiptContent = serde_json::from_value(json).unwrap();
        let formatter = ReceiptFormatter::new(80);
        let bytes = formatter.format_customer_receipt(&content);
        let text = String::from_utf8_lossy(&bytes);
        assert!(text.contains("Cod: 12345"));
    }
}
