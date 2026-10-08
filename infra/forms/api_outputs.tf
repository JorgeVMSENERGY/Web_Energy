output "forms_api_endpoints" {
  description = "Endpoints base de las APIs de formularios."

  value = {
    for environment, api in aws_apigatewayv2_api.forms :
    environment => api.api_endpoint
  }
}

output "forms_api_routes" {
  description = "Rutas publicas disponibles para el frontend."

  value = {
    for environment, api in aws_apigatewayv2_api.forms :
    environment => {
      health          = "${api.api_endpoint}/health"
      uploads_presign = "${api.api_endpoint}/uploads/presign"
      submissions     = "${api.api_endpoint}/submissions"
    }
  }
}