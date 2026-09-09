terraform {
  backend "s3" {
    bucket       = "vmsenergy-web-tfstate-787140332697"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}