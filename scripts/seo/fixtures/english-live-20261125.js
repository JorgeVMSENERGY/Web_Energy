function handler(event) {
    var request = event.request;

    if (request.uri === '/en' || request.uri === '/en/') {
        request.uri = '/en/index.html';
    }

    return request;
}