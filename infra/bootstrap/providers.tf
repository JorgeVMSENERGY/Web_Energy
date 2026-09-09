provider "aws" {
  region              = var.aws_region
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "VMS-Energy-Web"
      ProjectName = "Pagina-Web-VMS-Energy"
      Environment = "Production"
      Workload    = "Static-Website"
      ManagedBy   = "Terraform"
      Owner       = "TI"
    }
  }
}