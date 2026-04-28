## Copy Study Design

Follow these instructions to copy the study design board from a source study in one environment to a target study in any environment (dev/staging/production).

This will NOT copy the form definitions. Those would have to be manually uploaded after the copying is done. Event/Form OIDs (including random numbers at the end) will be preserved. See [OC-18941](https://jira.openclinica.com/browse/OC-18941) for more information. These instructions are based on the information available in this ticket.

### Retrieve Study Design Board

1. Go to study designer application for the source study (E.g. [https://cust2.design.openclinica-dev.io/b/rsLhXhxknHFPbge9z/test-cust2](https://cust2.design.openclinica-dev.io/b/rsLhXhxknHFPbge9z/test-cust2))  
2. Retrieve study designer board id for the source study. Board id is displayed between “/b/” and the next “/” in the url (“rsLhXhxknHFPbge9z” is the board id in the above example)  
3. Use a REST client (E.g. Postman) to retrieve the board.json (study definition) file

| GET {study.designer.url}/api/boards/{boardId}?showall=trueHeader: Authorization : Bearer \<accessToken\> |
| :---- |

4. Save the response of the above API in a board.json file

### Import Study Design Board

1. Go to study designer application for the target study (E.g. https://qe.design.staging.openclinica.io/b/CYwv8XJAWA36TdQPS/copy-of-dev-study)  
2. Retrieve study designer board id for the target study. Board id is displayed between “/b/” and the next “/” in the url (“CYwv8XJAWA36TdQPS” is the board id in the above example)  
3. Use a REST client (E.g. Postman) to import the board.json file retrieved from the source study into the target study

| POST {study.designer.url}/api/importStudy/{boardId}Headers:  Content-Type : application/json  Authorization : Bearer \<accessToken\>Body: \<paste raw contents of board.json into request body\> |
| :---- |

